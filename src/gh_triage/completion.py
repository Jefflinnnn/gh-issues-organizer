"""Shell completion scripts for ``triage``.

The scripts complete command names and, after ``triage remove``, the
repositories tracked in the current folder. They get those names from
``triage list --names`` so the list of tracked repositories has one source.
"""

BASH_SCRIPT = r"""# bash completion for triage.
# Load it with: eval "$(triage completion bash)"
_triage() {
  local cur=${COMP_WORDS[COMP_CWORD]} command="" i
  for (( i = 1; i < COMP_CWORD; i++ )); do
    case ${COMP_WORDS[i]} in
      --data-dir) (( i++ )) ;;
      -*) ;;
      *) command=${COMP_WORDS[i]}; break ;;
    esac
  done
  local IFS=$'\n'
  case $command in
    "")
      COMPREPLY=($(compgen -W $'add\nlist\nremove\ncompletion' -- "$cur")) ;;
    remove)
      COMPREPLY=($(compgen -W "$(triage list --names 2>/dev/null)" \
        -- "$cur")) ;;
    completion)
      COMPREPLY=($(compgen -W $'bash\nzsh' -- "$cur")) ;;
  esac
}
complete -o default -F _triage triage
"""

ZSH_SCRIPT = r"""#compdef triage
# zsh completion for triage.
# Load it with: eval "$(triage completion zsh)"
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }

_triage() {
  local -a commands repos
  local command="" i
  commands=(
    'add:track a repository and fetch its open issues'
    'list:show tracked repositories'
    'remove:stop tracking a repository and delete its data'
    'completion:print a shell completion script'
  )
  for (( i = 2; i < CURRENT; i++ )); do
    case ${words[i]} in
      --data-dir) (( i++ )) ;;
      -*) ;;
      *) command=${words[i]}; break ;;
    esac
  done
  case $command in
    "")
      _describe -t commands 'triage command' commands ;;
    remove)
      repos=(${(f)"$(triage list --names 2>/dev/null)"})
      compadd -a repos ;;
    completion)
      compadd bash zsh ;;
  esac
}
compdef _triage triage
"""

SCRIPTS = {"bash": BASH_SCRIPT, "zsh": ZSH_SCRIPT}
