# Shell profile for terminals opened inside Minecraft (Omo stations).
#
# Sourced on top of the normal ~/.bashrc (never replacing it), so the in-game
# shell behaves like the desktop one plus a few adjustments that only make
# sense on a ComputerCraft screen.

# Keep the user's own setup first.
if [ -f "$HOME/.bashrc" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.bashrc"
fi

# `hermes` on a bare command line prints a banner and drops to a plain
# prompt; the framed, colourful interface is `hermes --tui`. In here the TUI
# is what is wanted, so make it the default, and run every hermes command
# under the workspace's Hermes profile unless one is named explicitly.
hermes() {
    local profile=()
    if [ -n "$OMO_HERMES_PROFILE" ] && [ "$1" != "-p" ] && [ "$1" != "--profile" ]; then
        profile=(-p "$OMO_HERMES_PROFILE")
    fi
    if [ "$#" -eq 0 ] && [ -n "$OMO_HERMES_TITLE" ]; then
        # This tab has a saved conversation: resume it, never fork a new one.
        command "${OMO_HERMES_BIN:-hermes}" "${profile[@]}" chat --tui -c "$OMO_HERMES_TITLE" --create-if-missing
    elif [ "$#" -eq 0 ]; then
        command "${OMO_HERMES_BIN:-hermes}" "${profile[@]}" --tui
    else
        command "${OMO_HERMES_BIN:-hermes}" "${profile[@]}" "$@"
    fi
}

# A Claude Code or Codex tab: `omo_agent <claude|codex> <path> [session]`.
# The bridge passes the CLI it found (empty when none) and, for Claude, the
# tab's own session id: the first start creates that session, a revive or a
# restore after a restart resumes it. A missing CLI is explained here, with
# how to install it, and the tab stays an ordinary shell.
omo_agent() {
    local kind="$1" bin="$2" session="$3" name install
    case "$kind" in
        claude) name="Claude Code"
                install="curl -fsSL https://claude.ai/install.sh | bash   (or: npm install -g @anthropic-ai/claude-code)" ;;
        codex)  name="Codex"; install="npm install -g @openai/codex" ;;
        *)      return 1 ;;
    esac
    if [ -z "$bin" ] || [ ! -x "$bin" ]; then
        printf '\e[33m%s not found: %s is not installed (or not on the PATH of the Omo services).\e[0m\n' "$kind" "$name"
        printf 'Install it with:\n  %s\n' "$install"
        printf '\e[2mThen run %s here, or open a new %s tab from +. To use another copy, set OMO_%s_BIN for omo-bridge.\e[0m\n' \
            "$kind" "$name" "$(printf %s "$kind" | tr '[:lower:]' '[:upper:]')"
        return 127
    fi
    if [ "$kind" = claude ] && [ -n "$session" ]; then
        if ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/projects/*/"$session".jsonl >/dev/null 2>&1; then
            printf '\e[2m%s: resuming this tab'"'"'s session\e[0m\n' "$name"
            "$bin" --resume "$session"
        else
            printf '\e[2m%s\e[0m\n' "$name"
            "$bin" --session-id "$session"
        fi
    else
        printf '\e[2m%s\e[0m\n' "$name"
        "$bin"
    fi
}

# A CC screen is small; keep output readable.
export PAGER="less -R"
export LESS="-R"

alias grep='grep --color=auto'
# BSD ls and diff (older macOS) do not take --color.
ls --color=auto -d / >/dev/null 2>&1 && alias ls='ls --color=auto'
diff --color=auto /dev/null /dev/null >/dev/null 2>&1 && alias diff='diff --color=auto'

# Make it obvious which workspace/tab this shell belongs to.
if [ -n "$OMO_WORKSPACE" ]; then
    PS1='\[\e[38;5;208m\]${OMO_WORKSPACE_NAME:-omo}\[\e[0m\]:\[\e[38;5;39m\]\w\[\e[0m\]\$ '
fi

# A Hermes, Claude Code or Codex tab starts its agent the first time the shell opens.
# ONLY on the first start: OMO_AUTORUN is cleared right away, so if Hermes
# exits (or the user quits it) they land in a normal shell rather than an
# endless relaunch loop, and an interrupted tab is never silently re-run.
if [ -n "$OMO_AUTORUN" ]; then
    _omo_cmd="$OMO_AUTORUN"
    unset OMO_AUTORUN
    case "$_omo_cmd" in
        omo_agent\ *) ;;                # says what it starts itself
        *) printf '\e[2m%s\e[0m\n' "$_omo_cmd" ;;
    esac
    eval "$_omo_cmd"
    unset _omo_cmd
fi
