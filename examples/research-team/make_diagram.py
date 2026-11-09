"""Draw team-{light,dark}.svg for the research-team example (python3 make_diagram.py).

Thick solid arrows are xmsg messages between different harnesses; dashed arrows are a harness's own
messaging between two of its sessions (here Claude Code's SendMessage).
"""
import math
from pathlib import Path

W, H = 1280, 740
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "panel_line": "#d0d7de", "card": "#ffffff", "card_line": "#d0d7de",
        "text": "#1f2328", "muted": "#59636e", "xmsg": "#3d444d", "native": "#8c959f",
        "claude": "#c15f3c", "gemini": "#1a73e8", "openai": "#0e8a6a", "you": "#8250df",
    },
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "panel_line": "#30363d", "card": "#0d1117", "card_line": "#3d444d",
        "text": "#e6edf3", "muted": "#9198a1", "xmsg": "#d1d7e0", "native": "#7d8590",
        "claude": "#e8916d", "gemini": "#79b0ff", "openai": "#3fd1a4", "you": "#b083f0",
    },
}

PANELS = [  # x, width, title, subtitle
    (20, 360, "Experiment workspace", "runs/ · notebooks/ · hosts/"),
    (410, 520, "Pipeline package", "stages/ · defaults · releases"),
    (960, 300, "Upstream dependency", "vendored tool · pinned version"),
]
CARD_H = 98
CARDS = [  # x, y, width, family, role, title, meta
    (40, 222, 320, "gemini", "DIRECTOR", "Argon", "Gemini 4 Argon · agy session"),
    (40, 430, 320, "claude", "DRIVER", "Claude Code", "Claude Fable 5.1 · runs experiments"),
    (530, 222, 280, "claude", "STEWARD · RELEASE MANAGER", "Claude Code", "Claude Opus 5.5 · owns releases"),
    (430, 430, 232, "gemini", "REVIEW-SCIENCE", "Argon", "Gemini 4 Argon · agy session"),
    (678, 430, 232, "openai", "REVIEW-ENG", "Sol", "GPT-6.1 Sol · Codex thread"),
    (980, 222, 260, "claude", "UPSTREAM WATCHER", "Claude Code", "Claude Opus 5.5 · pin checks"),
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, c, size=13, weight=400, anchor="start", family=SANS, halo=None, spacing=None, rotate=None):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    if rotate is not None:
        extra += f' transform="rotate({rotate} {x} {y})"'
    if halo:
        extra += f' stroke="{halo}" stroke-width="6" stroke-linejoin="round" paint-order="stroke"'
    return (f'<text x="{x}" y="{y}" fill="{c}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{esc(s)}</text>')


def link(t, kind, x1, y1, x2, y2):
    """A two-way link: kind "xmsg" (thick, solid) or "native" (thin, dashed)."""
    if kind == "xmsg":
        style = f'stroke="{t["xmsg"]}" stroke-width="3.2"'
    else:
        style = f'stroke="{t["native"]}" stroke-width="1.8" stroke-dasharray="7 5"'
    m = f"url(#{kind})"
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" {style} marker-start="{m}" marker-end="{m}"/>'


def label_along(t, x1, y1, x2, y2, s, offset=11):
    """A label parallel to a line, offset to its upper side."""
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    length = math.hypot(x2 - x1, y2 - y1)
    nx, ny = (y2 - y1) / length, -(x2 - x1) / length
    lx, ly = round((x1 + x2) / 2 + offset * nx, 1), round((y1 + y2) / 2 + offset * ny, 1)
    return text(lx, ly, s, t["muted"], 14.5, 400, "middle", halo=t["panel"], rotate=round(ang, 1))


def draw(t: dict) -> str:
    alt = ("An example xmsg team: an Argon director and a Claude driver in the experiment workspace, a Claude "
           "steward with Argon and Sol reviewers on the pipeline package, and a Claude upstream watcher. Thick "
           "arrows are xmsg messages between harnesses; dashed arrows are Claude Code's own SendMessage between "
           "Claude sessions. Approvals stay with you.")
    head = 'viewBox="0 0 10 10" refX="8.5" refY="5" markerUnits="userSpaceOnUse" orient="auto-start-reverse"'
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'role="img" aria-label="{esc(alt)}">'),
        (f'<defs><marker id="xmsg" {head} markerWidth="14" markerHeight="14">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{t["xmsg"]}"/></marker>'
         f'<marker id="native" {head} markerWidth="11" markerHeight="11">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{t["native"]}"/></marker></defs>'),
        f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>',
    ]

    # You, and the approval bracket over the three repos (thin and solid: not a message link)
    out.append(f'<rect x="430" y="16" width="420" height="70" rx="35" fill="{t["card"]}" '
               f'stroke="{t["you"]}" stroke-width="1.6"/>')
    out.append(text(640, 45, "You", t["text"], 20, 700, "middle"))
    out.append(text(640, 70, "approve spend · releases · version pins", t["muted"], 15, 400, "middle"))
    out.append(f'<path d="M640,86 V112 M200,112 H1110 M200,112 V139 M670,112 V139 M1110,112 V139" '
               f'stroke="{t["you"]}" stroke-width="1.2" fill="none" opacity="0.7"/>')
    out.append(text(652, 104, "needs-user decisions come up; approvals go down", t["muted"], 13.5))

    # Repos the sessions work in
    for x, w, title, sub in PANELS:
        out.append(f'<rect x="{x}" y="140" width="{w}" height="420" rx="14" fill="{t["panel"]}" '
                   f'stroke="{t["panel_line"]}"/>')
        out.append(text(x + 20, 174, title, t["text"], 18, 700))
        out.append(text(x + 20, 198, sub, t["muted"], 13.5, 400, family=MONO))
    out.append(text(1110, 368, "watches upstream and flags", t["muted"], 14.5, 400, "middle"))
    out.append(text(1110, 390, "changes before any pin bump", t["muted"], 14.5, 400, "middle"))

    # Links (drawn before the cards so the arrowheads stop at the card edges)
    p = t["panel"]
    out.append(link(t, "xmsg", 200, 323, 200, 427))                      # director <-> driver
    out.append(text(214, 381, "results ↔ direction", t["muted"], 14.5, 400, halo=p))
    out.append(link(t, "xmsg", 363, 271, 527, 271))                      # director <-> steward
    out.append(text(445, 259, "results ↔ direction", t["muted"], 14.5, 400, "middle", halo=p))
    out.append(link(t, "native", 363, 469, 555, 324))                    # driver <-> steward
    out.append(label_along(t, 363, 469, 555, 324, "requests ↔ releases"))
    out.append(link(t, "xmsg", 640, 323, 566, 427))                      # steward <-> review-science
    out.append(link(t, "xmsg", 700, 323, 774, 427))                      # steward <-> review-eng
    out.append(text(670, 371, "pre-release", t["muted"], 14.5, 400, "middle", halo=p))
    out.append(text(670, 390, "review", t["muted"], 14.5, 400, "middle", halo=p))
    out.append(link(t, "native", 813, 271, 977, 271))                    # steward <-> upstream watcher
    out.append(text(895, 259, "upstream changes", t["muted"], 14.5, 400, "middle", halo=p))
    out.append(text(895, 295, "pin checks", t["muted"], 14.5, 400, "middle", halo=p))

    # Sessions
    for x, y, w, fam, role, title, meta in CARDS:
        c = t[fam]
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{CARD_H}" rx="10" fill="{t["card"]}" '
                   f'stroke="{t["card_line"]}"/>')
        out.append(f'<rect x="{x}" y="{y + 12}" width="4" height="{CARD_H - 24}" rx="2" fill="{c}"/>')
        out.append(text(x + 18, y + 27, role, c, 12.5, 700, spacing="0.5"))
        out.append(text(x + 18, y + 55, title, t["text"], 20, 700))
        out.append(text(x + 18, y + 80, meta, t["muted"], 14.5))

    # Legend and transports
    out.append(f'<rect x="20" y="584" width="1240" height="136" rx="14" fill="{t["panel"]}" '
               f'stroke="{t["panel_line"]}"/>')
    out.append(text(44, 642, "xmsg", t["text"], 28, 700, family=MONO))
    out.append(text(44, 668, "~/.xmsg", t["muted"], 14, 400, family=MONO))
    out.append(f'<line x1="172" y1="620" x2="218" y2="620" stroke="{t["xmsg"]}" stroke-width="3.2" '
               f'marker-end="url(#xmsg)"/>')
    out.append(text(232, 625, "xmsg message: between harnesses, recorded in the ledger", t["text"], 15))
    out.append(f'<line x1="680" y1="620" x2="726" y2="620" stroke="{t["native"]}" stroke-width="1.8" '
               f'stroke-dasharray="7 5" marker-end="url(#native)"/>')
    out.append(text(740, 625, "native: Claude Code's own SendMessage", t["text"], 15))
    chips = [("claude", "Claude Code: SendMessage, xmsg watch --next", 372),
             ("openai", "Codex: codex queue", 180),
             ("gemini", "agy: xmsg watch, or one headless turn", 328)]
    cx = 172
    for fam, label, w in chips:
        out.append(f'<rect x="{cx}" y="652" width="{w}" height="34" rx="17" fill="{t["card"]}" '
                   f'stroke="{t["card_line"]}"/>')
        out.append(f'<circle cx="{cx + 16}" cy="669" r="5" fill="{t[fam]}"/>')
        out.append(text(cx + 29, 674, label, t["text"], 14.5))
        cx += w + 12
    out.append(text(172, 708, "The approval lines from you are decisions, not messages.", t["muted"], 13))
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    for name, theme in THEMES.items():
        (here / f"team-{name}.svg").write_text(draw(theme))
        print(f"wrote {here / f'team-{name}.svg'}")
