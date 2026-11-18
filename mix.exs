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
    File.cp_r!(String.trim(prefix), ".python-runtime")
  end
end
