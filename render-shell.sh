# Sourced by Render's noninteractive start shell.
evoke_root=/opt/render/project/src
if [ -x "$evoke_root/.python-runtime/bin/python3" ]; then
  export PATH="$evoke_root/.python-runtime/bin:$PATH"
  export LD_LIBRARY_PATH="$evoke_root/.python-runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
