"""Cut static Regular (400) and Bold (700) TTFs from the Noto Sans TC variable font (throwaway spike code).

libass picks fonts by family name; the variable file reports family "Noto Sans TC Thin" (its default
instance), so `Fontname: Noto Sans TC` silently falls back to system fonts. Static instances fix that.
"""
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from common import SPIKE_OUT

FONTS = SPIKE_OUT / "fonts"
VF = FONTS / "NotoSansTC[wght].ttf"

for weight, style, out_name in ((400, "Regular", "NotoSansTC-Regular.ttf"), (700, "Bold", "NotoSansTC-Bold.ttf")):
    font = instancer.instantiateVariableFont(TTFont(VF), {"wght": weight}, updateFontNames=True)
    font.save(FONTS / out_name)
    check = TTFont(FONTS / out_name)
    print(out_name, "|", check["name"].getDebugName(1), "|", check["name"].getDebugName(2),
          "| usWeightClass", check["OS/2"].usWeightClass)
