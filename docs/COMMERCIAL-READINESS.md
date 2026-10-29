# Commercial readiness: do not announce a paid mod yet

This is an internal release gate, not legal advice. The repo is public (MIT)
and free releases are published on GitHub, the latest being
[v1.2.0](https://github.com/omoresearch/omo-space/releases/tag/v1.2.0)
(28 October 2026). The README shows a demo video, release install steps and
build-from-source steps; nothing is sold.

## The constraint

Mojang's current [Minecraft: Java Edition EULA](https://www.minecraft.net/en-us/eula)
says original Java mods can be distributed, but not sold for money or used to
make money from them. The [Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines)
also restrict mods that check access to an out-of-game product or service to
unlock in-game features. Do not put a price on the mod, lock the Hermes block
behind a paid entitlement or assume a bundled launcher makes this permissible.

The same guidelines allow screenshots of original Minecraft creations if they
do not imply official endorsement. Our README uses the prescribed prominent
"NOT AN OFFICIAL MINECRAFT PRODUCT" notice. Its images and loops are clips
from our own live stream (27 October 2026). No shader pack is distributed in
this repo or in any release bundle.

## Before a sales offer

1. Get a qualified review of the actual proposed offer, distribution method,
   launcher, optional Hermes/provider dependencies and brand presentation
   against the current EULA and Usage Guidelines. Written permission from
   Mojang/Microsoft may be needed for some commercial uses.
2. Harry chooses the product and price. No price, checkout, lead email sequence
   or paid access gate is authorized by this README.
3. Run a clean-machine install through Microsoft sign-in, then test the full
   place → work → quit → reconnect path. That has not been done yet for the
   public v1.2.0 bundles. Linux is tested end to end on a real machine; macOS
   and Windows are tested offline only (see [PLATFORMS.md](PLATFORMS.md)).
4. Check every redistributed dependency and media asset. The launcher downloads
   Minecraft, CC:T and MCEF from their own sources. Do not redistribute a
   modded Minecraft client or the Complementary shader zip.
5. Only then approve payments, a paid tier or paid campaigns.

The README and the free GitHub releases are a public product page and free
downloads with no price, checkout or paid gate, not a paid-mod listing. Any
paid offer would need its own approval and a compliant offer.
