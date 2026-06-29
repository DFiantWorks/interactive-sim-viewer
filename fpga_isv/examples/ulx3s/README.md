# ULX3S reference example

The bundled reference panel for **fpga-isv**: a photo of the
[ULX3S](https://github.com/ulx3s/ulx3s) FPGA board with the 8 user LEDs and the
direction / fire / power buttons mapped to their positions on the board.

```sh
fpga-isv --example ulx3s
```

Then run a matching interactive-sim simulation (e.g. the `ulx3s_demo` in the
[interactive-sim](https://github.com/DFiantWorks/interactive-sim) repo) so the sim
connects to the viewer (`INTERACTIVE_STREAM=host:port`). The 8-bit `leds` flag lights
the LED row; clicking a button (or its mirrored key) sends a momentary `1`/`0`.

- `ulx3s.json` — the panel config (LED/button pixel map, in original-image pixels).
- `ULX3S_v303_top.png` — the board photo (from the ULX3S project), committed so the
  example is self-contained and works offline.

To map a different photo, copy this config, point `image.url` at your image, and run
`fpga-isv --config your.json --calibrate` to read off pixel coordinates by clicking.
