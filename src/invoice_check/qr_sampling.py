"""Estimate a small, blurred QR's sampling grid from its three finder patterns.

This last, local pass models each pixel as the integral of blurred square
modules. Only a payload actually decoded by ZXing is returned. No invoice
fields, previously seen payloads or sample-specific locations are used.
"""
import math

import cv2
import numpy as np
import zxingcpp

# A dense optical solve is only useful for small, poorly sampled symbols.
_MAX_SIDE = 192
_MAX_MODULES = 65
_CORNER_FLAGS = ((0, 0), (1, 0), (1, 1), (0, 1))
_CDF_X = np.linspace(-5., 5., 1001)
_CDF = np.array([.5 + .5 * math.erf(x / math.sqrt(2)) for x in _CDF_X])


def _rectify(gray, quad):
    box = cv2.boxPoints(cv2.minAreaRect(quad.astype(np.float32)))
    box = np.roll(box, -int(np.argmin(box.sum(axis=1))), axis=0)
    if box[1, 0] < box[-1, 0]:
        box = box[[0, 3, 2, 1]]
    width = round(float(np.linalg.norm(box[1] - box[0])))
    height = round(float(np.linalg.norm(box[3] - box[0])))
    if min(width, height) < 20 or max(width, height) > _MAX_SIDE:
        return None
    pad = 8
    target = np.array([(pad, pad), (pad+width, pad),
                       (pad+width, pad+height), (pad, pad+height)], np.float32)
    matrix = cv2.getPerspectiveTransform(box, target)
    rect = cv2.warpPerspective(gray, matrix, (width+2*pad+1, height+2*pad+1),
                               flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return rect, (pad, pad, width, height), cv2.getPerspectiveTransform(target, box)


def _blurred_interval(points, start, end, sigma):
    """Gaussian optical response of one dark interval."""
    return (np.interp((points-start)/sigma, _CDF_X, _CDF) -
            np.interp((points-end)/sigma, _CDF_X, _CDF))


def _finder(xs, ys, center_x, center_y, pitch_x, pitch_y, sigma):
    out = np.zeros((len(ys), len(xs)))
    # The seven-module finder is three nested squares, black/white/black.
    for radius, contrast in ((3.5, 1), (2.5, -1), (1.5, 1)):
        dx, dy = radius * pitch_x, radius * pitch_y
        px = _blurred_interval(xs, center_x-dx, center_x+dx, sigma)
        py = _blurred_interval(ys, center_y-dy, center_y+dy, sigma)
        out += contrast * np.outer(py, px)
    return out


def _peak(correlation):
    y, x = np.unravel_index(correlation.argmax(), correlation.shape)

    def offset(values):
        a, b, c = values
        denom = a - 2*b + c
        return np.clip(.5*(a-c)/denom, -.75, .75) if denom < -.00001 else 0.

    dx = offset(correlation[y, x-1:x+2]) if 0 < x < correlation.shape[1]-1 else 0.
    dy = offset(correlation[y-1:y+2, x]) if 0 < y < correlation.shape[0]-1 else 0.
    return np.array((x+dx, y+dy))


def _grid_proposals(rect, box, check_cancel):
    x, y, w, h = box
    dark = (255-rect).astype(np.float32)
    proposals = []
    for count in range(21, min(_MAX_MODULES, round(min(w, h)/1.05))+1, 4):
        check_cancel()
        pitch = (w+h+2) / (2*count)
        if pitch > 7:
            continue
        inset = 3.5*pitch-.5
        corners = [(x+inset, y+inset), (x+w-inset, y+inset),
                   (x+w-inset, y+h-inset), (x+inset, y+h-inset)]
        radius = int(np.ceil(9*pitch)) // 2
        search = max(3, round(pitch*1.2))
        coords = np.arange(-radius, radius+1)
        best = None
        for sigma in (.4*pitch, .8*pitch):
            kernel = _finder(coords, coords, 0, 0, pitch, pitch,
                             math.sqrt(sigma*sigma+1/12)).astype(np.float32)
            centers, scores = [], []
            for cx, cy in corners:
                px, py = round(cx), round(cy)
                x0, y0 = max(0, px-radius-search), max(0, py-radius-search)
                patch = dark[y0:py+radius+search+1, x0:px+radius+search+1]
                if min(patch.shape) < len(coords):
                    break
                corr = cv2.matchTemplate(patch, kernel, cv2.TM_CCOEFF_NORMED)
                scores.append(float(corr.max()))
                centers.append(_peak(corr) + (x0+radius, y0+radius))
            if len(scores) == 4:
                missing = int(np.argmin(scores))
                good = np.delete(scores, missing)
                score = .6 * good.mean() + .4 * good.min()
                if score >= .50 and (best is None or score > best[0]):
                    best = score, count, missing, centers, sigma
        if best is not None:
            proposals.append(best)
    return sorted(proposals, key=lambda item: -item[0])[:7]


def _fit_finders(rect, box, proposal, check_cancel):
    _, count, missing, centers, sigma = proposal
    visible = [i for i in range(4) if i != missing]
    # Average the centers on each side to infer independent x/y spacing.
    boundaries = []
    pitches = []
    for axis in (0, 1):
        low, high = [np.mean([centers[i][axis] for i in visible
                              if _CORNER_FLAGS[i][axis] == side]) for side in (0, 1)]
        pitch = (high-low)/(count-7)
        pitches.append(pitch)
        boundaries.append((low-3.5*pitch, high+3.5*pitch))
    params = np.array([boundaries[0][0], boundaries[1][0],
                       boundaries[0][1], boundaries[1][1], sigma])
    patches, samples = [], []
    pitch = sum(pitches)/2
    for i in visible:
        cx, cy = centers[i]
        right, bottom = _CORNER_FLAGS[i]
        # Favor the quiet-zone side. Unknown data modules lie on the inner side.
        xl, xr = (2.8, 5.5) if right else (5.5, 2.8)
        yt, yb = (2.8, 5.5) if bottom else (5.5, 2.8)
        xs = np.arange(max(0, int(cx-pitch*xl)), min(rect.shape[1], int(cx+pitch*xr)+1))
        ys = np.arange(max(0, int(cy-pitch*yt)), min(rect.shape[0], int(cy+pitch*yb)+1))
        patches.append((xs, ys, right, bottom))
        samples.append(rect[np.ix_(ys, xs)].ravel())
    target = np.concatenate(samples).astype(float)
    x, y, w, h = box
    white = float(np.percentile(rect, 95))
    # Masking in QR balances dark and light modules over the symbol.
    amplitude = 2 * (white - rect[y+5:y+h-4, x+5:x+w-4].mean())
    if amplitude < 12:
        return None

    def residual(values):
        x0, y0, x1, y1, sigma = values
        dx, dy = (x1-x0)/count, (y1-y0)/count
        pred = []
        for xs, ys, right, bottom in patches:
            cx = x1-3.5*dx if right else x0+3.5*dx
            cy = y1-3.5*dy if bottom else y0+3.5*dy
            pred.append(_finder(xs, ys, cx, cy, dx, dy, max(.29, sigma)).ravel())
        dark = np.concatenate(pred)
        centered = dark-dark.mean()
        slope = np.dot(centered, target-target.mean()) / max(1e-6, np.dot(centered, centered))
        slope = np.clip(slope, -1.12*amplitude, -.88*amplitude)
        offset = np.clip(target.mean()-slope*dark.mean(), white-3, white+3)
        return target-(offset+slope*dark), offset, slope

    for _ in range(8):
        check_cancel()
        errors = residual(params)[0]
        jacobian = np.column_stack([(residual(params+delta*.03)[0]-errors)/.03 for delta in np.eye(5)])
        step = cv2.solve(jacobian, -errors[:, None], flags=cv2.DECOMP_SVD)[1].ravel()
        params += np.clip(step, -.5, .5)
        params[-1] = np.clip(params[-1], .29, 2.8)
        if np.max(np.abs(step)) < .01:
            break
    errors, offset, slope = residual(params)
    loss = np.mean(errors**2) / slope**2
    if loss < .023 and slope < -15:
        return loss, count, missing, params, offset, slope
    return None


def _sampling_matrix(length, edge, pitch, count, sigma):
    """Fraction of each pixel covered by each module, including Gaussian blur."""
    centers = np.arange(length)[:, None]
    starts = edge + pitch*np.arange(count)
    overlap = np.maximum(0, np.minimum(centers+.5, starts+pitch) - np.maximum(centers-.5, starts))
    return cv2.GaussianBlur(overlap, (1, 0), sigma, borderType=cv2.BORDER_REPLICATE)


def _read_modules(values, box, inverse):
    count = len(values)
    # Quiet zone and reduced grid are presentation for the decoder, not payload.
    modules = np.where(values > .5, 0, 255).astype(np.uint8)
    view = cv2.resize(np.pad(modules, 4, constant_values=255), None,
                      fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    for result in zxingcpp.read_barcodes(view, formats=zxingcpp.BarcodeFormat.QRCode,
                                         try_invert=False, try_downscale=False):
        if result.valid and result.bytes:
            pos = result.position
            corners = np.float32([(p.x, p.y) for p in (pos.top_left, pos.top_right,
                                pos.bottom_right, pos.bottom_left)])
            x0, y0, x1, y1 = box
            corners = (corners/4 - 4) * ((x1-x0)/count, (y1-y0)/count) + (x0, y0)
            original = cv2.perspectiveTransform(corners.astype(np.float32)[None], inverse)[0]
            return result.bytes, original
    return None


def _fixed_patterns(count, missing):
    """Known functional modules of a QR grid. Data and format bits stay unknown."""
    pattern = np.full((count, count), -1, np.int8)
    for x, y in ((0, 0), (count-7, 0), (0, count-7)):
        pattern[max(0, y-1):y+8, max(0, x-1):x+8] = 0
        pattern[y:y+7, x:x+7] = 1
        pattern[y+1:y+6, x+1:x+6] = 0
        pattern[y+2:y+5, x+2:x+5] = 1
    timing = np.arange(count-16) % 2 == 0
    pattern[6, 8:-8] = timing
    pattern[8:-8, 6] = timing
    # Versions 2 through 6 have one alignment pattern opposite the finders.
    if 21 < count < 45:
        i = count-7
        pattern[i-2:i+3, i-2:i+3] = 1
        pattern[i-1:i+2, i-1:i+2] = 0
        pattern[i, i] = 1
    # np.rot90 is counterclockwise, while our corner order is clockwise.
    return np.rot90(pattern, (2-missing) % 4)


def _sampling_axes(shape, count, params):
    left, top, right, bottom, sigma = params[:5]
    ay = _sampling_matrix(shape[0], top, (bottom-top)/count, count, max(.1, sigma))
    ax = _sampling_matrix(shape[1], left, (right-left)/count, count, max(.1, sigma))
    return ay, ax


def _refine_geometry(rect, count, params, colors):
    """Use the optical residual over the grid for one subpixel alignment step."""
    def residual(values):
        ay, ax = _sampling_axes(rect.shape, count, values)
        predicted = values[5] + values[6] * (ay @ colors @ ax.T)
        return (predicted-rect).ravel()

    errors = residual(params)
    jacobian = np.column_stack([(residual(params+.005*delta)-errors)/.005
                                for delta in np.eye(len(params))])
    step = cv2.solve(jacobian, -errors[:, None], flags=cv2.DECOMP_SVD)[1].ravel()
    limits = np.array([.1, .1, .1, .1, .1, 3., 3.])
    return params + np.clip(step, -limits, limits)


def _solve_modules(rect, fit, inverse, check_cancel):
    loss, count, missing, finder_params, white, contrast = fit
    params = np.r_[finder_params, white, contrast]
    # Remove approximate pixel-aperture variance from the finders' measured blur.
    params[4] = math.sqrt(max(params[4]**2 - 1/12, .01))
    pattern = _fixed_patterns(count, missing)
    known = pattern >= 0
    for refinement in range(2):
        ay, ax = _sampling_axes(rect.shape, count, params)
        sy, uy, vy = cv2.SVDecomp(ay)
        sx, ux, vx = cv2.SVDecomp(ax)
        dark = (rect.astype(float)-params[5])/params[6]
        transfer = np.outer(sy, sx)
        spectrum = (uy.T @ dark @ ux) * transfer
        power = transfer**2
        if refinement == 0:
            for noise_level in (.04, .012, .003, .11):
                check_cancel()
                estimate = vy.T @ (spectrum/(power+noise_level**2)) @ vx
                result = _read_modules(estimate, params[:4], inverse)
                if result is not None:
                    return result

        # Recover bounded reflectances and progressively enforce binary colors.
        left_gram, right_gram = ay.T @ ay, ax.T @ ax
        target = ay.T @ dark @ ax
        lipschitz = power[0, 0]
        values = np.clip(vy.T @ (spectrum/(power+.03**2)) @ vx, 0, 1)
        for strength in (0., .002, .006, .013, .026, .06):
            extrapolated = values
            for step in range(45):
                if step % 10 == 0:
                    check_cancel()
                gradient = (target - left_gram @ extrapolated @ right_gram) / lipschitz
                updated = np.clip(extrapolated + gradient + strength*(extrapolated-.5), 0, 1)
                updated[known] = pattern[known]
                extrapolated = updated + step/(step+3)*(updated-values)
                values = updated
            if strength >= .013:
                result = _read_modules(values, params[:4], inverse)
                if result is not None:
                    return result

        # Costly binary refinement is useful only for small, well-aligned symbols.
        if loss > .004 or max(rect.shape) > 128:
            break
        # Douglas–Rachford splitting alternates the binary grid constraint with a
        # regularized optical inverse. Average the settled predictions for noise.
        penalty = .009**2
        base = spectrum/(power+penalty)
        weight = penalty/(power+penalty)
        settled = np.zeros_like(values)
        for step in range(220):
            if step % 10 == 0:
                check_cancel()
            binary = (values > .5).astype(float)
            binary[known] = pattern[known]
            reflected = vy @ (2*binary-values) @ vx.T
            fitted = vy.T @ (base + reflected*weight) @ vx
            values += .85*(fitted-binary)
            if step >= 130:
                settled += binary
        colors = settled / 90
        result = _read_modules(colors, params[:4], inverse)
        if result is not None:
            return result
        if refinement == 0:
            params = _refine_geometry(rect, count, params, (colors > .5).astype(float))
    return None


def read_grid_patch(gray_crop, local_quad, check_cancel):
    """Return one checksum-validated (payload, oriented source quad), or None."""
    check_cancel()
    aligned = _rectify(gray_crop, local_quad)
    if aligned is None:
        return None
    rect, box, inverse = aligned
    fits = []
    for proposal in _grid_proposals(rect, box, check_cancel):
        fit = _fit_finders(rect, box, proposal, check_cancel)
        if fit is not None:
            fits.append(fit)
    for fit in sorted(fits, key=lambda entry: entry[0])[:2]:
        result = _solve_modules(rect, fit, inverse, check_cancel)
        if result is not None:
            return result
    return None
