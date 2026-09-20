NO_CLONE_INSTALL_URL = "https://loopx-project.github.io/loopx/install.sh"

DEFAULT_INSTALL_COMMAND = "python3 -m pip install --upgrade loopx"
DEFAULT_WORKFLOW_SKILL_INSTALL_COMMAND = "loopx workflow-skills --install"
DEFAULT_INSTALL_REPAIR_COMMAND = (
    f"{DEFAULT_INSTALL_COMMAND}\n"
    f"{DEFAULT_WORKFLOW_SKILL_INSTALL_COMMAND}\n"
    "loopx doctor"
)
ARCHIVE_FALLBACK_INSTALL_COMMAND = (
    f"curl -fsSL {NO_CLONE_INSTALL_URL} | bash\n"
    'export PATH="$HOME/.local/bin:$PATH"\n'
    "loopx doctor"
)
