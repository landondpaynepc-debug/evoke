# Compatibility with the existing Render service's build command, mix phx.digest.
defmodule Evoke.MixProject do
  use Mix.Project

  def project do
    [app: :evoke, version: "1.0.0", aliases: ["phx.digest": &install_python/1]]
  end

  defp install_python(_args) do
    {_output, status} = System.cmd("python", ["-m", "pip", "install", "--target", ".packages", "-r", "requirements.txt"], into: IO.stream())
    if status != 0, do: Mix.raise("Python dependency installation failed")
  end
end
