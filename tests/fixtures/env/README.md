# Hermetic gate environment

`appsettings.json` here is the config the golden-HTML gate renders against. It exists so the
gate does not depend on whatever happens to sit in the developer's own `APP_DIR` — without it,
`--check` and `--render-html` validate the working copy's live config and the result changes
from machine to machine.

Values are pinned to reproduce the receipt exactly as it rendered before Stage 3 made currency,
dates and labels configurable. That is deliberate: it keeps `golden.html` a true regression
detector across the configuration work. Neutral defaults and the other currency/date/tax
permutations are covered by unit tests, not by this fixture.

`Templates/` is intentionally absent — the app seeds it here from the bundled copies on first
use, which also exercises that path. It is gitignored.

**Do not edit to make a failing diff pass.** A moved golden means either a real regression or a
change that needs justifying in the commit message.
