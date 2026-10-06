"""Generate synthetic development images locally; never import private inputs.

The font and its hash are explicit. New fonts produce a different corpus, not a
replay of the published measurements. Inspect pixels before scoring OCR.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def generate(font_path, font_index, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Output directory must be empty; frozen fixtures are not overwritten')
    font_path = Path(font_path)
    rows = []
    for label, text in [('dense','鬱鑑龜齒體臺灣'), ('business','憑證帳簿總額'), ('latin','API-12345678')]:
        for pixels in (12,16,24):
            font = ImageFont.truetype(str(font_path), pixels, index=font_index)
            image = Image.new('RGB',(500,70),'white')
            ImageDraw.Draw(image).text((10,10),text,font=font,fill='black')
            ink = image.crop(Image.eval(image.convert('L'),lambda v:255-v).getbbox())
            for padding in (0,4,12):
                canvas = Image.new('RGB',(ink.width+2*padding,ink.height+2*padding),'white')
                canvas.paste(ink,(padding,padding))
                ident = f'{label}-{pixels}px-pad{padding}'
                path = output / (ident+'.png'); canvas.save(path)
                rows.append({'id':ident,'file':path.name,'ground_truth':text,'font_px':pixels,
                    'padding_px':padding,'source_hw':[canvas.height,canvas.width],
                    'image_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {'font_file':font_path.name,'font_sha256':hashlib.sha256(font_path.read_bytes()).hexdigest(),
                'font_index':font_index,'inputs':rows,
                'limitations':['Synthetic development set, not customer accuracy','Inspect font glyph coverage and raster pixels']}
    (output/'inputs.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font',type=Path,required=True)
    parser.add_argument('--font-index',type=int,default=0)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    result = generate(args.font,args.font_index,args.output_dir)
    print(f'Generated {len(result["inputs"])} local synthetic fixtures')


if __name__ == '__main__':
    main()
