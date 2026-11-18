# Compatibility with the existing Render service's build command, mix phx.digest.
defmodule Evoke.MixProject do
  use Mix.Project

  def project do
    [app: :evoke, version: "1.0.0", aliases: ["phx.digest": &install_python/1]]
  end

  defp install_python(_args) do
    {_output, status} = System.cmd("python", ["-m", "pip", "install", "--target", ".packages", "-r", "requirements.txt"], into: IO.stream())
    if status != 0, do: Mix.raise("Python dependency installation failed")
    # Render's Elixir runtime omits the Python toolchain from the running container.
    {prefix, 0} = System.cmd("python", ["-c", "import sys; print(sys.base_prefix)"])
    source = String.trim(prefix)
    File.cp_r!(source, ".python-runtime")
    # Resolve the executable even when the build image uses an absolute symlink.
    {binary, 0} = System.cmd("python", ["-c", "import os, sys; print(os.path.realpath(sys.executable))"])
    target = Path.expand(".python-runtime/bin/python3")
    if File.exists?(target) or File.read_link(target) != {:error, :enoent}, do: File.rm!(target)
    File.cp!(String.trim(binary), target)
    File.chmod!(target, 0o755)
    {_output, code} = System.cmd(target, ["-c", "import ssl, discord, aiohttp; print('Evoke runtime ready')"],
      env: [{"LD_LIBRARY_PATH", Path.expand(".python-runtime/lib")}, {"PYTHONPATH", Path.expand(".packages")}],
      into: IO.stream())
    if code != 0, do: Mix.raise("Bundled Evoke runtime check failed")
  end
end
