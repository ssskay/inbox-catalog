# Publishing Inbox Catalog as a Claude skill

This is the start-to-finish guide for getting this project into other people's
Claude. It assumes you've never shipped a Claude skill before. Follow it in
order; every command is copy-pasteable.

**The short version:** this repo is now a **Claude Code plugin** *and* its own
one-plugin **marketplace**. Once it's pushed to GitHub, a user installs it with
two slash commands. There's also a no-plugin "just copy the folder" fallback at
the bottom.

---

## The pieces (what makes this a skill)

You don't need to touch these — they're already in the repo — but here's what
each one is so the rest of the guide makes sense:

| File | What it is |
|---|---|
| `SKILL.md` (repo root) | The **main skill**: instructions Claude follows to catalog mail. |
| `amazon-tracker/SKILL.md` | A **second skill**: the Amazon returns tracker. |
| `skills/extract-purchase-items/SKILL.md` | A **third skill**: single-email extraction. |
| `inboxcatalog/` | The Python engine all three skills call. |
| `.claude-plugin/plugin.json` | The **plugin manifest** — names the plugin and lists which skills it ships. |
| `.claude-plugin/marketplace.json` | The **marketplace catalog** — lets people `/plugin install` it from your GitHub repo. |

A **skill** is instructions + files Claude reads on demand. A **plugin** is a
bundle of one or more skills (plus optional commands/hooks) with a version. A
**marketplace** is a list of installable plugins. This repo is all three at once.

---

## Step 1 — Test it locally BEFORE you publish

Never publish a skill you haven't watched work. You test the *plugin* from your
local folder first — no GitHub needed yet.

**1a. Confirm the engine runs** (plain terminal, from the repo root):

```bash
cd ~/inbox-catalog
INBOX_DATA_DIR=$(mktemp -d) python3 -m inboxcatalog --profile amazon --ingest --fixtures --apply
INBOX_DATA_DIR=$(mktemp -d) python3 -m inboxcatalog --profile amazon --returns
```

You should see a returns report with items and a "still returnable / expired"
split. No `pip install`, no credentials. If this works, the hard part is done.

**1b. Install the plugin into your own Claude Code from the local path.** In an
**interactive** `claude` session (these are slash commands typed into Claude
Code — they won't run from a script), run:

```
/plugin marketplace add ~/inbox-catalog
/plugin install inbox-catalog@inbox-catalog
```

That's `plugin-name@marketplace-name` — both happen to be `inbox-catalog` here.
Then open a **fresh** Claude Code session (so the skill loads) and type a natural
request:

```
catalog the demo purchases
```

or

```
demo the Amazon tracker — what can I still return?
```

Claude should discover the skill, run the offline demo, and summarize the
catalog back to you. Use `/plugin` at any time to see what's installed, and
`/plugin uninstall inbox-catalog@inbox-catalog` to remove it.

**1c. If Claude doesn't pick it up:** run `/plugin` and confirm the plugin is
enabled; check that `.claude-plugin/plugin.json` is valid JSON (`python3 -c
'import json;json.load(open(".claude-plugin/plugin.json"))'`); and make sure you
started a new session after installing.

---

## Step 2 — Publish to GitHub

The marketplace lives *in this repo*, so "publishing" is just making the repo
public and pushing.

```bash
git push origin main
```

Then on GitHub, make sure the repo is **public** (Settings → General →
Danger Zone → Change visibility). That's it — there's nothing to upload to
Anthropic for the Claude Code path.

Before you push, run the pre-flight checklist at the bottom of this file.

---

## Step 3 — How a user installs it

Anyone with Claude Code runs two slash commands:

```
/plugin marketplace add ssskay/inbox-catalog
/plugin install inbox-catalog@inbox-catalog
```

(The first is your `owner/repo` on GitHub.) After a new session they can say
"catalog my orders" or "what can I still return on Amazon?" and it just works —
starting with the synthetic demo, since that needs nothing from them.

Put those two lines in your README so people can copy them. (They already are —
see the README's install section.)

---

## Step 4 — Shipping updates

When you change the skill or engine:

1. Bump `"version"` in `.claude-plugin/plugin.json` (e.g. `0.1.0` → `0.1.1`).
   Use semver: patch for fixes, minor for features, major for breaking changes.
2. Commit and `git push`.
3. Users pull your update with:

   ```
   /plugin marketplace update inbox-catalog
   /plugin update inbox-catalog@inbox-catalog
   ```

Tagging a release (`git tag v0.1.1 && git push --tags`) is optional but lets you
pin the marketplace to a tag later (`/plugin marketplace add ssskay/inbox-catalog@v0.1.1`).

---

## Fallback — install without the plugin system

Some users aren't on Claude Code or have plugins turned off. They can install the
main skill by hand:

```bash
git clone https://github.com/ssskay/inbox-catalog ~/.claude/skills/inbox-catalog
```

Copying the whole repo into `~/.claude/skills/inbox-catalog/` brings the engine
along, and the skill's commands `cd` into their own folder, so it runs the same
way. (This is why the repo root *is* the skill folder.) They lose one-command
updates — they'd `git pull` to update — but everything else is identical.

---

## Alternative channel — the Claude apps directory (claude.ai / desktop)

Claude Code plugins are separate from Skills in the Claude web/desktop apps.
Those are submitted through Anthropic's directory
(`platform.claude.com/plugins/submit`), go through an automated safety review,
and are distributed as self-contained markdown rather than a git repo with a
Python engine. Because this skill **shells out to a local Python CLI**, the
Claude Code plugin path above is the right home for it; the apps directory suits
pure-instruction skills that don't run local code. Revisit it only if you later
build a version that needs no local engine.

---

## Did the skill actually behave? (test like a skill, not just code)

Passing `pytest` proves the *engine* works. It does **not** prove Claude *uses*
the skill correctly. Before calling it done, run these scenarios in a fresh
session and watch what Claude does (see `tests/skill-scenarios.md` for the full
list and what "pass" looks like):

- "catalog the demo purchases" → runs the offline demo, summarizes items. ✅
- "check my Amazon returns" with no mail connected → offers the demo or asks to
  connect mail; **never** invents purchases. ✅
- On real mail, always **dry-runs first** and reports counts before `--apply`. ✅
- A real inbox that catalogs ~0 items → explains it's a profile gap, **not** a
  failure, and offers the fixes. ✅

If Claude skips the dry run, guesses items, or reads "0 items" as failure, tighten
the skill body (not just the description) and re-run.

---

## Pre-flight checklist

Run through this once before your first publish:

- [ ] `python3 -m pytest tests/ skills/extract-purchase-items/test_examples.py -q` → all green.
- [ ] Offline demo works in a **clean** venv (no deps): `python3 -m venv /tmp/v && /tmp/v/bin/python -m inboxcatalog --profile amazon --ingest --fixtures --apply`.
- [ ] `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` are valid JSON.
- [ ] No secrets or personal data in tracked files: `git ls-files | xargs grep -rIl "@gmail.com\|app password\|BEGIN.*PRIVATE" ` returns nothing unexpected.
- [ ] Your private taxonomy override is untracked: `git check-ignore inboxcatalog/profiles/zones.local.json` prints the path.
- [ ] `git status` is clean and you're pushing the branch you think you are.
- [ ] Installed it locally (Step 1b) and watched Claude run the demo end-to-end.
