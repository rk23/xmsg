"""Draw every example's diagram-light.svg and diagram-dark.svg (python3 make_diagrams.py).

One visual language for all examples:
- thick solid arrow: an xmsg message between two different harnesses (recorded in the ledger);
- thin dashed arrow: a harness's own messaging between two of its sessions (e.g. Claude Code SendMessage);
- thin plain purple line: a decision from you (approvals), not a message.
"""
import math
from pathlib import Path

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
CARD_H = 98

THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "panel_line": "#d0d7de", "card": "#ffffff", "card_line": "#d0d7de",
        "text": "#1f2328", "muted": "#59636e", "xmsg": "#3d444d", "native": "#8c959f",
        "claude": "#c15f3c", "gemini": "#1a73e8", "openai": "#0e8a6a", "xai": "#9a6700", "any": "#6e7781",
        "you": "#8250df",
    },
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "panel_line": "#30363d", "card": "#0d1117", "card_line": "#3d444d",
        "text": "#e6edf3", "muted": "#9198a1", "xmsg": "#d1d7e0", "native": "#7d8590",
        "claude": "#e8916d", "gemini": "#79b0ff", "openai": "#3fd1a4", "xai": "#e3b341", "any": "#9198a1",
        "you": "#b083f0",
    },
}


class Svg:
    def __init__(self, t: dict, w: int, h: int, alt: str):
        self.t, self.w, self.h = t, w, h
        head = 'viewBox="0 0 10 10" refX="8.5" refY="5" markerUnits="userSpaceOnUse" orient="auto-start-reverse"'
        self.out = [
            (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
             f'role="img" aria-label="{esc(alt)}">'),
            (f'<defs><marker id="xmsg" {head} markerWidth="14" markerHeight="14">'
             f'<path d="M0,0 L10,5 L0,10 z" fill="{t["xmsg"]}"/></marker>'
             f'<marker id="native" {head} markerWidth="11" markerHeight="11">'
             f'<path d="M0,0 L10,5 L0,10 z" fill="{t["native"]}"/></marker></defs>'),
            f'<rect width="{w}" height="{h}" fill="{t["bg"]}"/>',
        ]

    def add(self, s: str) -> None:
        self.out.append(s)

    def text(self, x, y, s, color=None, size=14.5, weight=400, anchor="start", family=SANS, halo=False,
             spacing=None, rotate=None):
        c = self.t[color] if color in self.t else (color or self.t["muted"])
        extra = f' letter-spacing="{spacing}"' if spacing else ""
        if rotate is not None:
            extra += f' transform="rotate({rotate} {x} {y})"'
        if halo:
            extra += f' stroke="{self.t["panel"]}" stroke-width="6" stroke-linejoin="round" paint-order="stroke"'
        self.add(f'<text x="{x}" y="{y}" fill="{c}" font-family="{family}" font-size="{size}" '
                 f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{esc(s)}</text>')

    def link(self, kind, x1, y1, x2, y2, both=True):
        t = self.t
        if kind == "approval":
            self.add(f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{t["you"]}" stroke-width="1.2" opacity="0.7"/>')
            return
        style = (f'stroke="{t["xmsg"]}" stroke-width="3.2"' if kind == "xmsg"
                 else f'stroke="{t["native"]}" stroke-width="1.8" stroke-dasharray="7 5"')
        m = f"url(#{kind})"
        start = f' marker-start="{m}"' if both else ""
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" {style}{start} marker-end="{m}"/>')

    def label_along(self, x1, y1, x2, y2, s, offset=11):
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        length = math.hypot(x2 - x1, y2 - y1)
        nx, ny = (y2 - y1) / length, -(x2 - x1) / length
        lx, ly = round((x1 + x2) / 2 + offset * nx, 1), round((y1 + y2) / 2 + offset * ny, 1)
        self.text(lx, ly, s, "muted", 14.5, 400, "middle", halo=True, rotate=round(ang, 1))

    def panel(self, x, y, w, h, title, sub=None):
        t = self.t
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{t["panel"]}" '
                 f'stroke="{t["panel_line"]}"/>')
        self.text(x + 20, y + 34, title, "text", 18, 700)
        if sub:
            self.text(x + 20, y + 58, sub, "muted", 13.5, family=MONO)

    def card(self, x, y, w, accent, role, title, meta):
        t = self.t
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{CARD_H}" rx="10" fill="{t["card"]}" '
                 f'stroke="{t["card_line"]}"/>')
        self.add(f'<rect x="{x}" y="{y + 12}" width="4" height="{CARD_H - 24}" rx="2" fill="{t[accent]}"/>')
        self.text(x + 18, y + 27, role, accent, 12.5, 700, spacing="0.5")
        self.text(x + 18, y + 55, title, "text", 20, 700)
        self.text(x + 18, y + 80, meta, "muted", 14.5)

    def you(self, cx, y, w, sub):
        t = self.t
        self.add(f'<rect x="{cx - w / 2}" y="{y}" width="{w}" height="64" rx="32" fill="{t["card"]}" '
                 f'stroke="{t["you"]}" stroke-width="1.6"/>')
        self.text(cx, y + 27, "You", "text", 19, 700, "middle")
        self.text(cx, y + 50, sub, "muted", 14.5, 400, "middle")

    def legend(self, x, y, items):
        """items: (kind, label) pairs drawn left to right as line samples with labels."""
        t = self.t
        for kind, label in items:
            if kind == "approval":
                self.add(f'<line x1="{x}" y1="{y - 5}" x2="{x + 42}" y2="{y - 5}" stroke="{t["you"]}" '
                         f'stroke-width="1.4" opacity="0.8"/>')
            elif kind == "xmsg":
                self.add(f'<line x1="{x}" y1="{y - 5}" x2="{x + 42}" y2="{y - 5}" stroke="{t["xmsg"]}" '
                         f'stroke-width="3.2" marker-end="url(#xmsg)"/>')
            else:
                self.add(f'<line x1="{x}" y1="{y - 5}" x2="{x + 42}" y2="{y - 5}" stroke="{t["native"]}" '
                         f'stroke-width="1.8" stroke-dasharray="7 5" marker-end="url(#native)"/>')
            self.text(x + 56, y, label, "text", 14.5)
            x += 56 + len(label) * 7.6 + 40

    def svg(self) -> str:
        return "\n".join(self.out + ["</svg>"]) + "\n"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- generic examples

def director_and_executor(t):
    s = Svg(t, 1000, 372, "A director and an executor: you approve; the executor, a Claude Code or Codex session, "
                          "asks the director, an agy, Grok or Codex conversation, through xmsg and gets direction back.")
    s.you(225, 16, 300, "approve spend, merges, releases")
    s.panel(20, 112, 960, 200, "Your repo")
    s.link("approval", 225, 80, 225, 186)
    s.link("xmsg", 392, 235, 608, 235)
    s.text(500, 223, "results ↔ direction", anchor="middle", halo=True)
    s.card(60, 186, 330, "any", "EXECUTOR", "Claude Code or Codex", "does the work · request --wait")
    s.card(610, 186, 330, "any", "DIRECTOR", "agy, Grok or Codex", "any model · one headless turn")
    s.legend(40, 348, [("xmsg", "xmsg message between harnesses"), ("approval", "your decision, not a message")])
    return s.svg()


def builder_and_reviewer(t):
    s = Svg(t, 1000, 470, "A builder and a reviewer: a Claude Code builder exchanges review requests and findings "
                          "with a live Codex reviewer through xmsg, and test runs with a second Claude Code session "
                          "through Claude Code's own SendMessage.")
    s.panel(20, 16, 960, 384, "Your repo")
    s.link("xmsg", 392, 129, 608, 129)
    s.text(500, 117, "review request ↔ findings", anchor="middle", halo=True)
    s.link("native", 225, 180, 225, 276)
    s.text(239, 233, "test runs ↔ results", halo=True)
    s.card(60, 80, 330, "claude", "BUILDER", "Claude Code", "keeps xmsg watch --next running")
    s.card(610, 80, 330, "openai", "REVIEWER", "Codex", "live thread · codex queue")
    s.card(60, 278, 330, "claude", "TESTER", "Claude Code", "a second session")
    s.text(775, 330, "owns its review's status:", anchor="middle")
    s.text(775, 352, "accepted → running → done", "text", 14.5, 400, "middle", family=MONO)
    s.legend(40, 444, [("xmsg", "xmsg message between harnesses"), ("native", "native: Claude Code SendMessage")])
    return s.svg()


def review_board(t):
    s = Svg(t, 1000, 520, "A review board: you approve the release; the owner sends one xmsg request that fans out "
                          "to reviewers on three harnesses, a Codex thread, an agy conversation and a Grok "
                          "conversation, and each replies with findings.")
    s.you(500, 16, 280, "approve the release")
    s.panel(20, 112, 960, 340, "Package repo")
    s.link("approval", 500, 80, 500, 170)
    s.link("xmsg", 420, 270, 190, 340)
    s.link("xmsg", 500, 270, 500, 340)
    s.link("xmsg", 580, 270, 810, 340)
    s.text(514, 310, "review ↔ findings", halo=True)
    s.card(345, 170, 310, "any", "OWNER · RELEASE MANAGER", "Claude Code or Codex", "one request to role review")
    s.card(40, 342, 290, "openai", "REVIEWER A", "Codex", "live thread")
    s.card(355, 342, 290, "gemini", "REVIEWER B", "agy", "one headless turn")
    s.card(670, 342, 290, "xai", "REVIEWER C", "Grok", "one headless turn")
    s.legend(40, 494, [("xmsg", "xmsg message between harnesses"), ("approval", "your decision, not a message")])
    return s.svg()


# ---------------------------------------------------------------- the research team

RT_PANELS = [
    (20, 360, "Experiment workspace", "runs/ · notebooks/ · hosts/"),
    (410, 520, "Pipeline package", "stages/ · defaults · releases"),
    (960, 300, "Upstream dependency", "vendored tool · pinned version"),
]
RT_CARDS = [
    (40, 222, 320, "gemini", "DIRECTOR", "Argon", "Gemini 4 Argon · agy session"),
    (40, 430, 320, "claude", "DRIVER", "Claude Code", "Claude Fable 5.1 · runs experiments"),
    (530, 222, 280, "claude", "STEWARD · RELEASE MANAGER", "Claude Code", "Claude Opus 5.5 · owns releases"),
    (430, 430, 232, "gemini", "REVIEW-SCIENCE", "Argon", "Gemini 4 Argon · agy session"),
    (678, 430, 232, "openai", "REVIEW-ENG", "Sol", "GPT-6.1 Sol · Codex thread"),
    (980, 222, 260, "claude", "UPSTREAM WATCHER", "Claude Code", "Claude Opus 5.5 · pin checks"),
]


def research_team(t):
    s = Svg(t, 1280, 740,
            "An example xmsg team: an Argon director and a Claude driver in the experiment workspace, a Claude "
            "steward with Argon and Sol reviewers on the pipeline package, and a Claude upstream watcher. Thick "
            "arrows are xmsg messages between harnesses; dashed arrows are Claude Code's own SendMessage between "
            "Claude sessions. Approvals stay with you.")
    s.you(640, 18, 420, "approve spend · releases · version pins")
    s.add(f'<path d="M640,82 V112 M200,112 H1110 M200,112 V139 M670,112 V139 M1110,112 V139" '
          f'stroke="{t["you"]}" stroke-width="1.2" fill="none" opacity="0.7"/>')
    s.text(652, 104, "needs-user decisions come up; approvals go down", "muted", 13.5)
    for x, w, title, sub in RT_PANELS:
        s.panel(x, 140, w, 420, title, sub)
    s.text(1110, 368, "watches upstream and flags", anchor="middle")
    s.text(1110, 390, "changes before any pin bump", anchor="middle")
    s.link("xmsg", 200, 323, 200, 427)                         # director <-> driver
    s.text(214, 381, "results ↔ direction", halo=True)
    s.link("xmsg", 363, 271, 527, 271)                         # director <-> steward
    s.text(445, 259, "results ↔ direction", anchor="middle", halo=True)
    s.link("native", 363, 469, 555, 324)                       # driver <-> steward
    s.label_along(363, 469, 555, 324, "requests ↔ releases")
    s.link("xmsg", 640, 323, 566, 427)                         # steward <-> review-science
    s.link("xmsg", 700, 323, 774, 427)                         # steward <-> review-eng
    s.text(670, 371, "pre-release", anchor="middle", halo=True)
    s.text(670, 390, "review", anchor="middle", halo=True)
    s.link("native", 813, 271, 977, 271)                       # steward <-> upstream watcher
    s.text(895, 259, "upstream changes", anchor="middle", halo=True)
    s.text(895, 295, "pin checks", anchor="middle", halo=True)
    for x, y, w, fam, role, title, meta in RT_CARDS:
        s.card(x, y, w, fam, role, title, meta)
    s.add(f'<rect x="20" y="584" width="1240" height="136" rx="14" fill="{t["panel"]}" '
          f'stroke="{t["panel_line"]}"/>')
    s.text(44, 642, "xmsg", "text", 28, 700, family=MONO)
    s.text(44, 668, "~/.xmsg", "muted", 14, family=MONO)
    s.legend(172, 625, [("xmsg", "xmsg message: between harnesses, recorded in the ledger"),
                        ("native", "native: Claude Code's own SendMessage")])
    cx = 172
    for fam, label, w in (("claude", "Claude Code: SendMessage, xmsg watch --next", 372),
                          ("openai", "Codex: codex queue", 180),
                          ("gemini", "agy: xmsg watch, or one headless turn", 328)):
        s.add(f'<rect x="{cx}" y="652" width="{w}" height="34" rx="17" fill="{t["card"]}" '
              f'stroke="{t["card_line"]}"/>')
        s.add(f'<circle cx="{cx + 16}" cy="669" r="5" fill="{t[fam]}"/>')
        s.text(cx + 29, 674, label, "text", 14.5)
        cx += w + 12
    s.text(172, 708, "The approval lines from you are decisions, not messages.", "muted", 13)
    return s.svg()


DIAGRAMS = {
    "director-and-executor": director_and_executor,
    "builder-and-reviewer": builder_and_reviewer,
    "review-board": review_board,
    "research-team": research_team,
}

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    for name, draw in DIAGRAMS.items():
        for theme, palette in THEMES.items():
            path = here / name / f"diagram-{theme}.svg"
            path.write_text(draw(palette))
            print(f"wrote {path.relative_to(here)}")
