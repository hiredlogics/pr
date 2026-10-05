"""Render FRONTEND_LIVE_TEST notice page images for browser upload."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "reports" / "live" / "p174_fixtures"


def _font(size: int = 18):
    for name in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_page(text: str, path: Path, *, title: str = "NOTICE") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(img)
    font = _font(20)
    bold = _font(28)
    draw.text((40, 30), title, fill="black", font=bold)
    y = 90
    for line in text.strip().splitlines():
        draw.text((40, y), line[:90], fill="black", font=font)
        y += 28
    img.save(path, format="JPEG", quality=92)
    return path


def late_ntk_pages(pcn: str = "FRONTEND_LIVE_TEST_CPPLUS") -> dict[str, Path]:
    front = f"""PARKING CHARGE NOTICE
Operator Name: LIVE_TEST Parking Ltd
PCN Number: {pcn}
Vehicle Registration: LT12 EST
Location: LIVE_TEST Retail Park
Postcode: M1 1AA
Date of Contravention: 01/06/2026
Date of Issue: 20/06/2026
Entry Time: 10:00
Exit Time: 12:47
Charge: £100
Alleged Breach: Overstayed paid time
Trade Association: BPA
"""
    back = f"""NOTICE TO KEEPER — REVERSE
PCN {pcn}
Schedule 4 Protection of Freedoms Act 2012
If you were not the driver, you may pass this notice to the driver or name them.
We are seeking recovery from the keeper under Schedule 4 if the driver is not identified.
Pay or appeal within 28 days of the date of issue.
"""
    unrelated = """UNRELATED DOCUMENT
Council tax bill summary
Account 998877
This is not a parking notice reverse page.
"""
    front_path = render_page(front, FIX / f"{pcn}_front.jpg", title="FRONT")
    # Second file with same pixels but a different name/size key so the UI
    # accepts two uploads (UploadStep dedupes on name:size).
    dup = FIX / f"{pcn}_front_dup.jpg"
    from PIL import Image
    img = Image.open(front_path).convert("RGB")
    # tiny invisible change so size differs
    img.putpixel((0, 0), (254, 254, 254))
    img.save(dup, format="JPEG", quality=92)
    return {
        "front": front_path,
        "front_dup": dup,
        "back": render_page(back, FIX / f"{pcn}_back.jpg", title="REVERSE"),
        "unrelated": render_page(unrelated, FIX / f"{pcn}_unrelated.jpg", title="OTHER"),
    }


CP_PLUS_NARRATIVE = (
    "FRONTEND_LIVE_TEST I attended the car park for shopping at the retail estate. "
    "Partway through I realised I had forgotten my purse at home, "
    "so I left the site. I returned later the same day to continue my visit."
)


if __name__ == "__main__":
    paths = late_ntk_pages()
    for k, p in paths.items():
        print(k, p, p.stat().st_size)
