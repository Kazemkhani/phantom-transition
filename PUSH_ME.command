#!/bin/bash
# Double-click this file in Finder, or run: bash PUSH_ME.command
# It creates the GitHub repo and pushes. Nothing else on your machine is touched.
set -e
cd "$(dirname "$0")"

echo "Creating and pushing phantom-transition..."
echo

if ! command -v gh >/dev/null 2>&1; then
  echo "The GitHub CLI is not installed. Install it with:"
  echo "    brew install gh"
  echo "then run this file again."
  exit 1
fi

gh auth status >/dev/null 2>&1 || gh auth login

git init -q 2>/dev/null || true
git add -A
git -c user.name="Amir Hossein Kazemkhani" -c user.email="amir@amirkazemkhani.com" \
    commit -q -m "Phantom transitions in phase-gated multi-agent voice systems

A minimal reproduction of a failure mode where a phase-transition tool call
issued before end of speech still executes after the caller barges in, silently
advancing the conversation to a stage the caller never triggered.

Three fixes: cancel the handoff after execution on interrupted turns, disable
pre-emptive generation at source, and guard phase progression at the tool layer
rather than by prompt instruction. Ten tests establish the guard has no bypass
path reachable through the public API." || echo "(nothing new to commit)"

git branch -M main
gh repo create phantom-transition --public --source=. --remote=origin --push \
  --description "A reproduction and fix for phantom phase transitions in interruptible multi-agent voice systems"

echo
echo "Done. Your repo URL:"
gh repo view --json url -q .url
echo
echo "Paste that URL into the two email drafts and the CVs where it says ADD GITHUB URL."
