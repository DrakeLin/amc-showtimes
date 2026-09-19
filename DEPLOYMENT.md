# Showtimes migration status — 2026-09-19

Prepared on branch `feat/vercel-owner-favorites`. **Initial deployment READY; configuration incomplete.**

- Vercel Hobby project `drakelin18-5415/showtimes`, deployed through Vercel Drop.
- Production URL https://showtimes-one.vercel.app/ opened and Showtimes UI loaded.
- Deployment ID `GvSoAP9VaXLFpAhWSdt53AGkeUoB`.
- Private `showtimes-blob` store created and connected; token variable exists.
- CRON_SECRET saved as a Production secret.
- AMC_VENDOR_KEY and OWNER_ACCESS_KEY still required; redeploy after configuration.
- Google Cloud console was unavailable, preventing authorized transfer of existing AMC key.
- Production is not yet verified as a functioning showtime service.

- Implemented Vercel Flask entrypoint, private Blob adapter, daily cron, CDN build,
  owner-only favorites, NewPark/Mercado name resolution, and progressive day fetches.
- Preserved the previously restored theater filters and legacy Cloud Run backend.
- 75 backend tests pass; Python compile and JavaScript syntax checks pass.
- Real AMC catalog/showtime access and real Blob read/write/claim behavior
  still require live verification. Vercel build completed and public UI loaded.
- Cloud browser refused localhost (`ERR_BLOCKED_BY_CLIENT`), so no visual QA is claimed.
- Vercel plugin tools were exposed but account scope/deploy operations failed.
  User-authorized browser fallback successfully created the deployment.
- GitHub access fixed after user expanded installation to all repositories.
  Remote branch `feat/vercel-owner-favorites` created successfully.
- Local environment has no AMC vendor key, Vercel token, or Blob token. Retrieve the
  existing AMC vendor key through authorized Cloud Run configuration access; never
  commit it or use a client-side environment variable.
- Existing Google production services and GCS objects were not changed. No paid upgrade.

## Finish when access is available

1. Recheck Vercel tool availability, account plan (Hobby), and existing projects.
2. Publish the prepared branch once GitHub write access is functional, or deploy the
   local source through supported Vercel deployment tooling. Do not overwrite newer
   upstream changes. Use `showtimes` or an available generic project/domain variant.
3. Connect a private Blob store and set the server-only variables listed in README.
4. Deploy with Fluid Compute, the 300-second duration, and one daily cron.
5. Verify catalog parsing and exact NewPark/Mercado matches with the real AMC key.
6. Verify owner login, unauthorized PUT rejection, cross-session favorites persistence,
   a theater with uncached data, retry after upstream failure, and metadata enrichment.
7. Verify Blob's create-without-overwrite behavior for simultaneous refresh claims.
8. Warm the initial snapshots via the authenticated cron route, inspect runtime logs,
   and verify the returned production URL in a browser. Only then call it live.
9. Discuss shutting down the old Google resources after successful cutover; do not
   delete them while they are the only working deployment.

Fandango's public UI confirms theater/location discovery; it does not document its
internal refresh architecture. The implemented design uses AMC's official API,
small durable caches, and optional metadata enrichment rather than scraping Fandango.
