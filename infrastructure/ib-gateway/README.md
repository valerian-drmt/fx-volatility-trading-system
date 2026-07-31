# Local IB Gateway container

The published `ghcr.io/unusualalpha/ib-gateway:stable` and `:latest` tags
lag behind IB's mandatory version bumps — on 2026-04-22 both refused
login with "version X is no longer supported". Until the maintainer
rebuilds, the workaround is to build the image **locally** from the
official IB installer.

## One-off setup (local-only, not committed)

1. Clone the upstream Dockerfile repo into this folder :
   ```powershell
   cd infrastructure/ib-gateway
   git clone https://github.com/UnusualAlpha/ib-gateway-docker upstream
   ```
2. Download the Linux IB Gateway installer from
   <https://www.interactivebrokers.com/en/trading/ibgateway-latest.php>
   (the `.sh` standalone Linux x64, not the `.exe` Windows). Place the
   file in `upstream/latest/` and rename to match the version, e.g.
   `ibgateway-10.46.1c-standalone-linux-x64.sh`.
3. Download the IBC release zip from
   <https://github.com/IbcAlpha/IBC/releases>, e.g. `IBCLinux-3.23.0.zip`,
   place in `upstream/latest/`.
4. In `upstream/latest/Dockerfile` :
   - Bump `ENV IB_GATEWAY_VERSION` and `ENV IBC_VERSION` to match the
     filenames above.
   - Replace the `curl` downloads (IB + IBC) with `COPY` referencing the
     local files. Drop the sha256 check.
   - Add `RUN sed -i 's/\r$//' /root/scripts/*.sh` right after
     `COPY ./scripts /root/scripts` to strip CRLF line endings introduced
     by Git on Windows — otherwise `exec /root/scripts/run.sh: no such
     file or directory` at container start.
5. Point `docker-compose.yml` service `ib-gateway` at the local build :
   ```yaml
   ib-gateway:
     build:
       context: ./infrastructure/ib-gateway/upstream/latest
       dockerfile: Dockerfile
     image: fxvol-ib-gateway:local
   ```
6. Build + run :
   ```powershell
   docker compose --profile ib build ib-gateway
   docker compose --profile ib up -d ib-gateway
   ```

## Why we don't commit the installer or the clone

- IB installer licence : redistribution forbidden
- Binary size : 320 MB of installer, not suitable for Git
- The `upstream/` folder is a clone of a third-party repo, maintained
  separately

Both are listed in `.gitignore`.

## Long-term fix

Two cleaner paths than re-doing these steps at every upstream bump :

1. **Switch to the actively-maintained fork**
   `ghcr.io/gnzsnz/ib-gateway:latest` — rebuilds monthly, drop-in
   replacement of the same env vars (TWS_USERID, TWS_PASSWORD, etc.).
2. **Own Dockerfile from scratch** : based on `eclipse-temurin:17-jdk`,
   with Xvfb + x11vnc + IBC pinned. ~150 lines, maintained in our tree.
   The installer download still requires manual handling (licence), but
   nothing else is third-party.

Either becomes a proper post-R9 PR once R8 is on main.

## Pinned image + the persistent volume (READ before bumping the version)

The gnzsnz image installs the Gateway into `/home/ibgateway/Jts/ibgateway/<build>/`,
and compose persists **that whole path** as the named volume `ib_gateway_jts`
(the volume also holds the API/Trusted-IPs settings we want to keep). Docker
copies the image's contents into a named volume **only once — when the volume
is empty**. So if the image tag floats (`:latest`) and a deploy pulls a newer
Gateway build, the volume keeps the *old* jars and IBC dies at boot with:

```
Error: Offline TWS/Gateway version <new> is not installed: can't find jars folder
```

The container then crash-loops, `4002` never opens, and market-data / vol-engine /
risk-engine stall `unhealthy` (their healthchecks are Redis heartbeat freshness,
which goes stale when IB is unreachable). This bit prod on 2026-07-31
(image bumped 10.48.1e → 10.49.1c, volume stuck on 10.48.1e).

Because of this, `IB_GATEWAY_IMAGE` is **pinned to a specific build**, not
`:latest` — both in `infrastructure/ec2/remote-deploy.sh` (prod `.env`) and as
the compose default. Bumping it is a deliberate two-step change:

1. Update the pin in both places to the new tag (e.g. `:10.50.1x`).
2. Refresh the volume's install so the new jars land next to the kept settings.
   Either recreate the volume (loses the Trusted-IPs GUI config → redo it over
   VNC 127.0.0.1:5900), **or** the non-destructive surgical copy:

   ```sh
   VOL=fxvol_ib_gateway_jts
   IMG=ghcr.io/gnzsnz/ib-gateway:<new-tag>
   sudo docker stop fxvol-ib-gateway
   # backup first
   sudo docker run --rm -u root -v $VOL:/vol -v /tmp:/b alpine \
     tar czf /b/ibgw_jts_$(date -u +%Y%m%dT%H%M%SZ).tgz -C /vol .
   # copy the new build's install into the volume (settings untouched)
   sudo docker run --rm -u root -v $VOL:/vol --entrypoint sh $IMG \
     -c 'cp -a /home/ibgateway/Jts/ibgateway/<new-build> /vol/ibgateway/'
   sudo docker start fxvol-ib-gateway     # IBC logs in, 4002 opens, engines reconnect
   ```
