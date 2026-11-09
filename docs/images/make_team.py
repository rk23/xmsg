"""Draw docs/images/team-{light,dark}.svg: the example research team (python3 make_team.py)."""
from pathlib import Path

W, H = 1280, 716
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "panel_line": "#d0d7de", "card": "#ffffff", "card_line": "#d0d7de",
        "text": "#1f2328", "muted": "#59636e", "arrow": "#6e7781",
        "claude": "#c15f3c", "gemini": "#1a73e8", "openai": "#0e8a6a", "you": "#8250df",
    },
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "panel_line": "#30363d", "card": "#0d1117", "card_line": "#3d444d",
        "text": "#e6edf3", "muted": "#9198a1", "arrow": "#8b949e",
        "claude": "#e8916d", "gemini": "#79b0ff", "openai": "#3fd1a4", "you": "#b083f0",
    },
}

PANELS = [  # x, width, title, subtitle
    (20, 360, "Experiment workspace", "runs/ · notebooks/ · hosts/"),
    (410, 520, "Pipeline package", "stages/ · defaults · releases"),
    (960, 300, "Upstream dependency", "vendored tool · pinned version"),
]
CARDS = [  # x, y, width, family, role, title, meta
    (40, 222, 320, "gemini", "DIRECTOR", "Argon", "Gemini 4 Argon · agy session"),
    (40, 430, 320, "claude", "DRIVER", "Claude Code", "Claude Fable 5.1 · runs experiments"),
    (530, 222, 280, "claude", "STEWARD · RELEASE MANAGER", "Claude Code", "Claude Opus 5.5 · owns releases"),
    (430, 430, 232, "gemini", "REVIEW-SCIENCE", "Argon", "Gemini 4 Argon · agy session"),
    (678, 430, 232, "openai", "REVIEW-ENG", "Sol", "GPT-6.1 Sol · Codex thread"),
    (980, 222, 260, "claude", "UPSTREAM WATCHER", "Claude Code", "Claude Opus 5.5 · pin checks"),
]
CARD_H = 84


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, c, size=13, weight=400, anchor="start", family=SANS, halo=None, spacing=None):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    if halo:
        extra += f' stroke="{halo}" stroke-width="5" stroke-linejoin="round" paint-order="stroke"'
    return (f'<text x="{x}" y="{y}" fill="{c}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{esc(s)}</text>')


def arrow(x1, y1, x2, y2, c, both=True):
    start = ' marker-start="url(#head)"' if both else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" stroke-width="1.6"'
            f'{start} marker-end="url(#head)"/>')


def draw(t: dict) -> str:
    out = [(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="An example xmsg team: an Argon director and a Claude driver in the experiment '
            f'workspace, a Claude steward with Argon and Sol reviewers on the pipeline package, and a Claude '
            f'upstream watcher, all connected through xmsg, with approvals kept by you.">'),
           (f'<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{t["arrow"]}"/></marker></defs>'),
           f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>']

    # You, and the approval bracket over the three repos
    out.append(f'<rect x="470" y="20" width="340" height="62" rx="31" fill="{t["card"]}" '
               f'stroke="{t["you"]}" stroke-width="1.6"/>')
    out.append(text(640, 46, "You", t["text"], 17, 700, "middle"))
    out.append(text(640, 67, "approve spend · releases · version pins", t["muted"], 12.5, 400, "middle"))
    dash = f'stroke="{t["you"]}" stroke-width="1.4" stroke-dasharray="4 4" fill="none"'
    out.append(f'<path d="M640,82 V112 M200,112 H1110 M200,112 V138 M670,112 V138 M1110,112 V138" {dash}/>')
    out.append(text(652, 104, "needs-user decisions come up; approvals go down", t["muted"], 11.5))

    # Repos the sessions sit on
    for x, w, title, sub in PANELS:
        out.append(f'<rect x="{x}" y="140" width="{w}" height="420" rx="14" fill="{t["panel"]}" '
                   f'stroke="{t["panel_line"]}"/>')
        out.append(text(x + 20, 172, title, t["text"], 15, 700))
        out.append(text(x + 20, 193, sub, t["muted"], 11.5, 400, family=MONO))
    out.append(text(1110, 352, "watches upstream and flags", t["muted"], 12, 400, "middle"))
    out.append(text(1110, 370, "changes before any pin bump", t["muted"], 12, 400, "middle"))

    # Flows (drawn before the cards so arrowheads stop at card edges)
    a, p = t["arrow"], t["panel"]
    out.append(arrow(200, 308, 200, 428, a))
    out.append(text(212, 372, "results ↔ direction", t["muted"], 12, 400, halo=p))
    out.append(arrow(362, 264, 528, 264, a))
    out.append(text(445, 254, "results ↔ direction", t["muted"], 12, 400, "middle", halo=p))
    out.append(arrow(362, 452, 556, 309, a))
    out.append(text(470, 404, "requests ↔ releases", t["muted"], 12, 400, "middle", halo=p))
    out.append(arrow(640, 308, 562, 428, a))
    out.append(arrow(700, 308, 778, 428, a))
    out.append(text(670, 360, "pre-release", t["muted"], 12, 400, "middle", halo=p))
    out.append(text(670, 376, "review", t["muted"], 12, 400, "middle", halo=p))
    out.append(arrow(812, 264, 978, 264, a))
    out.append(text(895, 254, "upstream changes", t["muted"], 12, 400, "middle", halo=p))
    out.append(text(895, 284, "pin checks", t["muted"], 12, 400, "middle", halo=p))

    # Sessions
    for x, y, w, fam, role, title, meta in CARDS:
        c = t[fam]
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{CARD_H}" rx="10" fill="{t["card"]}" '
                   f'stroke="{t["card_line"]}"/>')
        out.append(f'<rect x="{x}" y="{y + 12}" width="4" height="{CARD_H - 24}" rx="2" fill="{c}"/>')
        out.append(text(x + 18, y + 25, role, c, 11, 700, spacing="0.6"))
        out.append(text(x + 18, y + 49, title, t["text"], 17, 700))
        out.append(text(x + 18, y + 69, meta, t["muted"], 12.5))

    # xmsg underneath everything
    out.append(f'<path d="M200,560 V592 M670,560 V592 M1110,560 V592" stroke="{t["arrow"]}" '
               f'stroke-width="1.4" stroke-dasharray="2 4"/>')
    out.append(f'<rect x="20" y="592" width="1240" height="104" rx="14" fill="{t["panel"]}" '
               f'stroke="{t["panel_line"]}"/>')
    out.append(text(44, 636, "xmsg", t["text"], 24, 700, family=MONO))
    out.append(text(44, 660, "~/.xmsg", t["muted"], 12, 400, family=MONO))
    out.append(text(170, 630, "Every arrow is an xmsg message: one address book, mailboxes, threads, read receipts, "
                    "a request ledger, the sender's model.", t["text"], 13.5))
    chips = [("claude", "Claude Code: SendMessage + watch --next", 292),
             ("openai", "Codex: codex queue", 150),
             ("gemini", "agy: mailbox watch or one headless turn per message", 368)]
    cx = 170
    for fam, label, w in chips:
        out.append(f'<rect x="{cx}" y="646" width="{w}" height="28" rx="14" fill="{t["card"]}" '
                   f'stroke="{t["card_line"]}"/>')
        out.append(f'<circle cx="{cx + 15}" cy="660" r="4.5" fill="{t[fam]}"/>')
        out.append(text(cx + 27, 664.5, label, t["text"], 12.5))
        cx += w + 12
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    for name, theme in THEMES.items():
        (here / f"team-{name}.svg").write_text(draw(theme))
        print(f"wrote {here / f'team-{name}.svg'}")
