# Third-party assets

The project MIT license covers original code/documentation only. Bundled GSAP files and the externally hosted font retain their upstream
copyright and license terms.

| Files | Author / source | License |
| --- | --- | --- |
| `web/vendor/gsap.min.js`, `web/vendor/ScrollTrigger.min.js` (3.15.0) | GreenSock / Webflow, [GSAP](https://gsap.com/), [npm distribution](https://registry.npmjs.org/gsap) | [Standard No Charge GSAP License](https://gsap.com/community/standard-license/), effective April 30, 2025, modified May 30, 2025 |
| Satoshi (loaded from the official Fontshare CDN; not included in the repository) | Satoshi by Indian Type Foundry, via [Fontshare](https://www.fontshare.com/fonts/satoshi) | [ITF Free Font License](https://www.fontshare.com/licenses/itf-ffl) |

GSAP's original copyright/license headers are retained in both JS files. It
animates this application interface and is not MIT-licensed by this repository.
Satoshi remains subject to the ITF Free Font License v2.0 (August 17, 2026).
The repository does not redistribute the font binary. CSS requests the original
font from Fontshare; a system font is used if it is unavailable. This font
request uses the browser network, independently of the OCI worker proxy.
Consult upstream terms before downloading, self-hosting or changing its use.
Exact download sources are also in [web/vendor/SOURCES.txt](web/vendor/SOURCES.txt).

## Packaged Python runtime and libraries

The source project depends on the official [Oracle Cloud Infrastructure Python
SDK](https://github.com/oracle/oci-python-sdk), pinned in `requirements.txt`.
The SDK's Universal Permissive License 1.0 / Apache License 2.0 notices and its
vendored third-party notices remain applicable. Source installs may still use a
separately installed OCI CLI as a compatibility fallback; release packages do
not require or bundle the CLI.

GitHub Releases include CPython (PSF license and its third-party notices), the
OCI SDK, cryptography/OpenSSL, certificates, and their runtime dependencies.
Builds collect installed distribution license/notice files into `licenses/`,
alongside `DEPENDENCIES.json` containing exact versions and upstream metadata.
Those licenses apply independently of this project's MIT license. SDK transitives
include cryptography, pyOpenSSL, certifi, python-dateutil, pytz, circuitbreaker,
urllib3, PyJWT, python-pkcs11, crc32c, aiohttp and their dependencies; the actual
manifest in each platform package is authoritative for that build.

The [PyInstaller](https://pyinstaller.org/en/stable/license.html) bootloader and
runtime use GPL-2.0-or-later with the PyInstaller bootloader exception, allowing
distribution of the bundled application under its own license. Its license and
exception text are retained in release notices. PyInstaller is a build-time
tool, not a change to this project's source license.

Oracle and Ampere product names belong to their owners.
