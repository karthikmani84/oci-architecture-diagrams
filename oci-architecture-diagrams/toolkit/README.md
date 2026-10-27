# Oracle toolkit goes here

This folder is intentionally empty. The OCI icons and style files belong to Oracle and are not redistributed in this repo.

1. Download the **OCI Architecture Diagram Toolkit** (draw.io format) from Oracle:
   https://docs.oracle.com/en-us/iaas/Content/General/Reference/graphicsfordiagrams.htm
2. Put the downloaded file in this folder as it is. Any of these works:
   - the "OCI Style Guide for Drawio" `.zip`
   - `OCI Architecture Diagram Toolkit v24.x.drawio`
   - `OCI Library.xml` (adds the VCN/subnet and connector badges)
3. Zip the whole `oci-architecture-diagrams` folder and upload it to Claude.

The script finds the files automatically. To check: `python3 ../scripts/ocidraw.py icons drg`
