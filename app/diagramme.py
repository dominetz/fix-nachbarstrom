"""Einfache SVG-Diagramme, ohne externe Bibliotheken.

Farben über CSS-Variablen (--reihe-1 …), damit sie an einer Stelle in basis.html
festgelegt sind. Jeder Balken und Punkt hat einen Tooltip (<title>).
"""
from html import escape

MONATSNAMEN = ["Jän", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _zahl(x: float, stellen: int = 0) -> str:
    return f"{x:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _schritt(maximum: float) -> float:
    """Runde Achsenschritte (1, 2, 5 × 10^n) für etwa vier Gitterlinien."""
    if maximum <= 0:
        return 1
    roh = maximum / 4
    basis = 10 ** len(str(int(roh))) / 10 if roh >= 1 else 0.1
    for f in (1, 2, 2.5, 5, 10):
        if roh <= f * basis:
            return f * basis
    return 10 * basis


def legende(reihen: list[tuple[str, str]]) -> str:
    teile = [f'<span class="leg"><i style="background:var({farbe})"></i>{escape(name)}</span>' for name, farbe in reihen]
    return f'<div class="legende">{"".join(teile)}</div>'


def balken(beschriftungen: list[str], reihen: list[tuple[str, str, list[float]]], einheit: str = "kWh",
           hoehe: int = 240) -> str:
    """Gestapelte Balken. reihen: (Name, CSS-Variable, Werte)."""
    breite, links, unten, oben = 600, 46, 26, 10
    n = max(len(beschriftungen), 1)
    summen = [sum(r[2][i] for r in reihen) for i in range(len(beschriftungen))]
    schritt = _schritt(max(summen, default=0))
    maximum = schritt * max(1, -(-max(summen, default=0) // schritt))
    flaeche = hoehe - unten - oben
    spalte = (breite - links) / n
    balkenbreite = min(36, spalte * 0.6)
    teile = [f'<svg class="diagramm" viewBox="0 0 {breite} {hoehe}" role="img" aria-label="Balkendiagramm">']
    y = 0.0
    while y <= maximum + 1e-9:
        py = oben + flaeche - y / maximum * flaeche
        teile.append(f'<line x1="{links}" x2="{breite}" y1="{py:.1f}" y2="{py:.1f}" class="gitter"/>'
                     f'<text x="{links - 6}" y="{py + 4:.1f}" class="achse" text-anchor="end">{_zahl(y)}</text>')
        y += schritt
    for i, text in enumerate(beschriftungen):
        x = links + spalte * i + (spalte - balkenbreite) / 2
        basis = oben + flaeche
        sichtbar = [(name, farbe, werte[i]) for name, farbe, werte in reihen if werte[i] > 0]
        for j, (name, farbe, wert) in enumerate(sichtbar):
            h = wert / maximum * flaeche
            luecke = 2 if j < len(sichtbar) - 1 else 0
            rund = 4 if j == len(sichtbar) - 1 else 0
            teile.append(f'<path d="{_balkenpfad(x, basis - h, balkenbreite, max(h - luecke, 0.5), rund)}" '
                         f'style="fill:var({farbe})" class="marke"><title>{escape(text)}: {escape(name)} '
                         f'{_zahl(wert)} {einheit}</title></path>')
            basis -= h
        teile.append(f'<text x="{x + balkenbreite / 2:.1f}" y="{hoehe - 8}" class="achse" '
                     f'text-anchor="middle">{escape(text)}</text>')
        # unsichtbare, breitere Trefferfläche mit Gesamtwert
        teile.append(f'<rect x="{links + spalte * i:.1f}" y="{oben}" width="{spalte:.1f}" height="{flaeche}" '
                     f'class="treffer"><title>{escape(text)}: '
                     + "; ".join(f"{escape(n)} {_zahl(w[i])} {einheit}" for n, _, w in reihen)
                     + '</title></rect>')
    teile.append(f'<line x1="{links}" x2="{breite}" y1="{oben + flaeche}" y2="{oben + flaeche}" class="basis"/></svg>')
    return legende([(n, f) for n, f, _ in reihen]) + "".join(teile)


def _balkenpfad(x: float, y: float, b: float, h: float, r: float) -> str:
    r = min(r, b / 2, h)
    return (f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"L{x + b - r:.1f},{y:.1f} Q{x + b:.1f},{y:.1f} {x + b:.1f},{y + r:.1f} L{x + b:.1f},{y + h:.1f} Z")


def tageskurve(reihen: list[tuple[str, str, list[float], bool]], einheit: str = "kW", hoehe: int = 240) -> str:
    """Verlauf über einen Tag. reihen: (Name, CSS-Variable, 96 Werte in kWh je Viertelstunde, als Fläche).
    Gezeichnet wird die Leistung in kW (kWh je Viertelstunde × 4)."""
    breite, links, unten, oben = 600, 46, 26, 10
    flaeche = hoehe - unten - oben
    maximum_roh = max((max(w) * 4 for _, _, w, _ in reihen if w), default=0)
    schritt = _schritt(maximum_roh)
    maximum = schritt * max(1, -(-maximum_roh // schritt))
    dx = (breite - links) / 95

    def punkt(i, w):
        return links + i * dx, oben + flaeche - w * 4 / maximum * flaeche

    teile = [f'<svg class="diagramm" viewBox="0 0 {breite} {hoehe}" role="img" aria-label="Tagesverlauf">']
    y = 0.0
    while y <= maximum + 1e-9:
        py = oben + flaeche - y / maximum * flaeche
        teile.append(f'<line x1="{links}" x2="{breite}" y1="{py:.1f}" y2="{py:.1f}" class="gitter"/>'
                     f'<text x="{links - 6}" y="{py + 4:.1f}" class="achse" text-anchor="end">{_zahl(y, 1 if schritt < 1 else 0)}</text>')
        y += schritt
    for stunde in range(0, 25, 3):
        x = links + stunde * 4 * dx if stunde < 24 else breite
        teile.append(f'<text x="{min(x, breite - 14):.1f}" y="{hoehe - 8}" class="achse" text-anchor="middle">{stunde:02d}:00</text>')
    for name, farbe, werte, als_flaeche in reihen:
        pfad = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in
                        enumerate(punkt(i, w) for i, w in enumerate(werte)))
        if als_flaeche:
            teile.append(f'<path d="{pfad} L{links + 95 * dx:.1f},{oben + flaeche} L{links},{oben + flaeche} Z" '
                         f'style="fill:var({farbe});opacity:.35"/>')
        teile.append(f'<path d="{pfad}" style="stroke:var({farbe})" class="linie"/>')
    # Tooltips je Stunde
    for stunde in range(24):
        q = stunde * 4
        text = f"{stunde:02d}:00–{stunde + 1:02d}:00: " + "; ".join(
            f"{escape(n)} {_zahl(sum(w[q:q + 4]), 2)} kWh" for n, _, w, _ in reihen)
        teile.append(f'<rect x="{links + q * dx:.1f}" y="{oben}" width="{4 * dx:.1f}" height="{flaeche}" '
                     f'class="treffer"><title>{text}</title></rect>')
    teile.append(f'<line x1="{links}" x2="{breite}" y1="{oben + flaeche}" y2="{oben + flaeche}" class="basis"/></svg>')
    return legende([(n, f) for n, f, _, _ in reihen]) + "".join(teile)


def monatsname(schluessel: str) -> str:
    """'2026-03' -> 'Mär 26'"""
    jahr, monat = schluessel.split("-")
    return f"{MONATSNAMEN[int(monat) - 1]} {jahr[2:]}"
