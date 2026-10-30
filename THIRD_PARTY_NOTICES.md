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

Oracle Cloud Infrastructure CLI is installed separately, not vendored here.
See [Oracle's repository](https://github.com/oracle/oci-cli) for source/license.
Oracle and Ampere product names belong to their owners.
