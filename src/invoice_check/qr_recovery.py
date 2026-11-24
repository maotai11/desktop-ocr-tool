"""Bounded local optical recovery for small QR symbols in a captured screen."""
import cv2
import numpy as np
import zxingcpp


_MAX_LOCALIZATION_PIXELS = 500_000
_MAX_PATCH_SIDE = 320
_MAX_PATCHES = 4


def _candidate_quads(gray):
    h, w = gray.shape
    scale = min(1., (_MAX_LOCALIZATION_PIXELS / (h * w)) ** .5)
    preview = gray if scale == 1. else cv2.resize(
        gray, (max(1, round(w * scale)), max(1, round(h * scale))),
        interpolation=cv2.INTER_AREA)
    _, dark = cv2.threshold(preview, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    # Connect the small separated modules while retaining the white quiet zone.
    kernel_side = 5 if scale == 1. else 3
    connected = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((kernel_side, kernel_side), np.uint8))
    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if min(bw, bh) < 20 * scale or max(bw, bh) > (_MAX_PATCH_SIDE-16) * scale:
            continue
        rect = cv2.minAreaRect(contour)
        short, long = sorted(rect[1])
        if short < .7 * long or short <= 0 or cv2.contourArea(contour) < .58 * short * long:
            continue
        # A defocused QR can have >95% "dark" occupancy at the page's
        # global threshold. Require internal texture rather than a black ratio.
        margin = max(2, min(bw, bh)//8)
        interior = preview[y+margin:y+bh-margin, x+margin:x+bw-margin]
        if not interior.size or float(cv2.meanStdDev(interior)[1][0, 0]) < 3.:
            continue
        quad = cv2.boxPoints(rect)
        quad *= [w / preview.shape[1], h / preview.shape[0]]
        # Consistent cyclic order for fitting the module grid of rotated paper.
        quad = np.roll(quad, -int(np.argmin(quad.sum(axis=1))), axis=0)
        candidates.append(quad)
        if len(candidates) > _MAX_PATCHES:
            # A single invoice selection is expected; don't process arbitrary
            # subsets of a dense multi-code screen.
            return []
    return candidates


def _inverse_filtered(crop, check_cancel):
    """Wiener inverse of a Gaussian point-spread function with a noise floor."""
    # Reflect the patch at its edges to keep the DFT's periodic boundary from
    # injecting a dark line through the QR. Only small, local patches reach here.
    border = 10
    source = cv2.copyMakeBorder(crop, border, border, border, border, cv2.BORDER_REFLECT_101)
    source = cv2.normalize(source, None, 0., 255., cv2.NORM_MINMAX, cv2.CV_32F)
    spectrum = cv2.dft(source, flags=cv2.DFT_COMPLEX_OUTPUT)
    h, w = source.shape
    fy = np.fft.fftfreq(h).astype(np.float32)
    fx = np.fft.fftfreq(w).astype(np.float32)
    exponent = (-2 * np.pi**2) * (fy[:, None]**2 + fx[None, :]**2)
    # Short and longer blur radii span roughly one to four original pixels.
    # The first pass regularizes more strongly for noise and JPEG ringing.
    for sigma, regularization in ((.85, .002), (1., .00003), (1.35, .00008), (1.5, .00002)):
        check_cancel()
        transfer = np.exp(exponent * sigma**2)
        inverse = transfer / (transfer**2 + regularization)
        recovered = cv2.idft(spectrum * inverse[:, :, None],
                             flags=cv2.DFT_SCALE | cv2.DFT_REAL_OUTPUT)
        recovered = np.clip(recovered[border:-border, border:-border], 0, 255).astype(np.uint8)
        yield cv2.resize(recovered, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)


def recover_patches(gray, decoded_corners, check_cancel):
    """Yield only checksum-valid QR observations with original screen coordinates."""
    for quad in _candidate_quads(gray):
        check_cancel()
        center = tuple(map(float, quad.mean(axis=0)))
        if any(cv2.pointPolygonTest(np.asarray(known, np.float32), center, False) >= 0
               for known in decoded_corners):
            continue
        lo = np.floor(quad.min(axis=0) + 1e-4).astype(int)
        hi = np.ceil(quad.max(axis=0) - 1e-4).astype(int)
        left, top = max(0, lo[0]-6), max(0, lo[1]-6)
        crop = gray[top:hi[1]+7, left:hi[0]+7]
        if not crop.size or max(crop.shape) > _MAX_PATCH_SIDE:
            continue
        offset = np.array([left, top], dtype=np.float32)
        recovered = False
        for view in _inverse_filtered(crop, check_cancel):
            check_cancel()
            results = zxingcpp.read_barcodes(view, formats=zxingcpp.BarcodeFormat.QRCode,
                                             try_invert=False, try_downscale=False)
            for result in results:
                if result.valid and result.bytes:
                    pos = result.position
                    corners = np.array([(p.x, p.y) for p in (pos.top_left, pos.top_right,
                                             pos.bottom_right, pos.bottom_left)], dtype=np.float32)
                    yield result.bytes, offset + corners/3
                    recovered = True
            if recovered:
                break
        if not recovered:
            from .qr_sampling import read_grid_patch
            check_cancel()
            result = read_grid_patch(crop, quad-offset, check_cancel)
            if result is not None:
                payload, corners = result
                yield payload, corners+offset
