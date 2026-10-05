# Scanning Tunneling Microscopy (STM) add-ons

Tools that work alongside Nanonis.

```
conda env create -f environment.yml
conda activate stm-addons
python launch.py cartographer             # add --simulate to run without Nanonis
```

| tool | |
|---|---|
| [cartographer](cartographer/README.md) | tracks the coarse moves of the tip and shows them on a map (work in progress) |
