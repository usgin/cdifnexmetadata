#!/usr/bin/env python3
"""Build an NXxas file laid out the way the NeXus manual sketches it.

`manual/source/examples/xas.txt` in the NeXus definitions repository
sketches a temperature-dependent XAS file. The repository ships no XAS
data, so the sketch is the only record of this layout, and it differs
from every real file in `exampleData/` in two ways that matter to the
crosswalk:

  * `monochromator` sits directly under `NXentry`, not under
    `NXinstrument` -- the crosswalk states
    `/ENTRY:NXentry/INSTRUMENT:NXinstrument/monochromator:NXmonochromator/energy`
  * the detectors are named `I` and `I0`, not `itrans` and `i0` --
    detector names are the discriminating case in `_match_groups`

so this file is here to show what the extractor does with a layout it
was not written against.

The sketch is followed exactly, with two deliberate departures, both
noted in the file itself:

  * `definition = "NXxas"` is added. The sketch has none, and without it
    nothing identifies the technique.
  * the sketch links `I0_data/data` to `/entry/instrument/I00/data`, a
    detector that does not exist. That is a typo in the manual; the link
    points at `I0` here, since a broken link would test nothing.

The measured values are synthetic -- a plausible Fe K edge, not real
data. Nothing here should be cited as a measurement.

Usage:
    python tools/make_nxxas_manual_example.py [-o PATH]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import h5py
import numpy as np

SOURCE = ("NeXus definitions manual/source/examples/xas.txt "
          "(XraySpectroscopy/nexus_definitions)")

NE, NT = 61, 3


def spectra():
    """A synthetic Fe K-edge mu(E) at three temperatures."""
    energy = np.linspace(7000.0, 7300.0, NE)
    temperature = np.array([100.0, 200.0, 300.0])
    i0 = 1.0e6 * np.exp(-(energy - 7000.0) / 4000.0)
    # edge step at 7112 eV, broadened a little more as temperature rises
    mu = np.empty((NE, NT))
    for j, t in enumerate(temperature):
        width = 2.0 + t / 100.0
        step = 1.0 / (1.0 + np.exp(-(energy - 7112.0) / width))
        decay = 0.35 * np.exp(-(energy - 7112.0).clip(0) / 180.0)
        mu[:, j] = 0.4 + 1.1 * step + decay * np.sin(
            (energy - 7112.0).clip(0) / 12.0)
    transmitted = i0[:, None] * np.exp(-mu)
    return energy, temperature, i0[:, None] * np.ones(NT), transmitted


def build(path: Path) -> None:
    energy, temperature, i0, transmitted = spectra()
    with h5py.File(path, "w") as f:
        f.attrs["default"] = "entry"
        entry = f.create_group("entry")
        entry.attrs["NX_class"] = "NXentry"
        entry.attrs["default"] = "I_data"
        entry["title"] = "Temperature-dependent Fe K-edge XAS (synthetic)"
        # Not in the sketch; without it nothing says which technique.
        entry["definition"] = "NXxas"
        entry["start_time"] = "2026-07-28T00:00:00Z"

        instrument = entry.create_group("instrument")
        instrument.attrs["NX_class"] = "NXinstrument"

        # The sketch's naming: I and I0, not itrans and i0.
        for name, values in (("I", transmitted), ("I0", i0)):
            det = instrument.create_group(name)
            det.attrs["NX_class"] = "NXdetector"
            data = det.create_dataset("data", data=values)
            data.attrs["units"] = "counts"
            det["energy"] = h5py.SoftLink("/entry/monochromator/energy")
            det["temperature"] = h5py.SoftLink("/entry/sample/temperature")

        sample = entry.create_group("sample")
        sample.attrs["NX_class"] = "NXsample"
        sample["name"] = "Fe foil"
        temp = sample.create_dataset("temperature", data=temperature)
        temp.attrs["units"] = "K"

        # The departure from every real file: monochromator under the
        # entry rather than under the instrument.
        mono = entry.create_group("monochromator")
        mono.attrs["NX_class"] = "NXmonochromator"
        mono_energy = mono.create_dataset("energy", data=energy)
        mono_energy.attrs["units"] = "eV"

        for name, target in (("I_data", "/entry/instrument/I/data"),
                             ("I0_data", "/entry/instrument/I0/data")):
            nxdata = entry.create_group(name)
            nxdata.attrs["NX_class"] = "NXdata"
            nxdata.attrs["signal"] = "data"
            nxdata.attrs["axes"] = ["energy", "temperature"]
            nxdata.attrs["energy_indices"] = 0
            nxdata.attrs["temperature_indices"] = 1
            nxdata["data"] = h5py.SoftLink(target)
            nxdata["energy"] = h5py.SoftLink("/entry/monochromator/energy")
            nxdata["temperature"] = h5py.SoftLink("/entry/sample/temperature")

        entry["provenance"] = (
            f"Structure from {SOURCE}. Values are synthetic. "
            "definition=NXxas added (the sketch has none); the sketch's "
            "I0_data link to /entry/instrument/I00/data is a typo and "
            "points at I0 here.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=Path,
                    default=Path("exampleData/NXxas-manual-sketch.hdf5"))
    args = ap.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build(args.out)
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
