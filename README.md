# x402-openapi

OpenAPI 3.0.3 spec for the [Manteclaw x402 API Service](https://manteclaw-x402.fly.dev)
— 200 endpoints (199 pay-per-call + free `/health`) on Base, priced in USDC
via x402 v2.

The spec is **generated fresh from the live service catalog**, not
hand-written: `generate-spec.py` reads
`https://manteclaw-x402.fly.dev/.well-known/agent-catalog.json` and emits
`openapi.yaml`. Re-running it after the service adds or reprices endpoints
regenerates the spec. Nothing here comes from the stale 9-endpoint spec that
shipped with the dead base-l2-agent-kit.

## Files

| File | What it is |
| --- | --- |
| `openapi.yaml` | The generated OpenAPI 3.0.3 spec (200 paths, 18 tags). |
| `generate-spec.py` | Reads the catalog, emits the spec. |
| `validate-spec.py` | Validates the spec against the catalog. |
| `README.md` | This file. |

## What's in the spec

- **All 200 endpoints**: 199 paid + free `/health`, grouped into 18 tags
  (analytics, chain, defi, dex, gas, governance, lending, memes, naming,
  nft, oracles, prediction, security, stablecoins, status, token, utils,
  wallet).
- **Prices on every paid operation**: each paid `get` carries an
  `x-payment-info` extension with the exact x402 v2 payment terms —
  `scheme: exact`, `network: eip155:8453` (Base), `asset` (USDC
  `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`), `amount` in USDC atomic
  units (6 decimals: $0.005 = `5000`), `payTo`, `maxTimeoutSeconds: 300`,
  and the human-readable `priceUsdc`.
- **Path parameters**: all 127 parameterized endpoints declare their
  parameters (`required: true`) with types, patterns (e.g. `^0x[0-9a-fA-F]{40}$`
  for addresses) and real example values taken from the catalog's preview
  endpoints.
- **Responses**: `200` (JSON), `402` (Payment Required — terms in the
  base64 `payment-required` header), `400`, `429`, `500`.
- **Root `x-x402` extension**: facilitator (`https://facilitator.payai.network`),
  network, asset, payee.

Price bands: $0.001 ×10, $0.005 ×77, $0.01 ×106, $0.02 ×6, free ×1.

## Regenerate

```bash
python3 generate-spec.py            # fetch the live catalog, write openapi.yaml
python3 generate-spec.py --input /path/to/agent-catalog.json   # local catalog
python3 generate-spec.py --output spec.yaml                    # other output
```

Requires PyYAML (`pip install pyyaml`). Standard library otherwise.
Output is deterministic: regenerating from the live catalog and from the
local catalog file produces byte-identical YAML.

## Validate

```bash
python3 validate-spec.py                          # vs live catalog
python3 validate-spec.py --spec spec.yaml --input catalog.json
```

Checks: valid YAML + OpenAPI 3.0.x; path set matches the catalog exactly;
every paid operation has correct `x-payment-info` (scheme, network, asset,
amount, payee, price); `/health` has none; every path parameter declared;
operationIds unique. Exits 0 on pass, 1 with a list of failures.

## Generate a typed client

Any OpenAPI 3 generator works. Examples:

```bash
# Python client (openapi-generator-cli)
npx @openapitools/openapi-generator-cli generate \
  -i openapi.yaml -g python -o ./client-python

# TypeScript client
npx @openapitools/openapi-generator-cli generate \
  -i openapi.yaml -g typescript-fetch -o ./client-ts
```

Note: generated clients call the endpoints plainly and get HTTP 402 back.
Paying is a separate step — sign an EIP-3009 `transferWithAuthorization`
for the `x-payment-info.amount` and retry with the signature in the
`X-PAYMENT` header (see the service's how-to-call guide at
<https://github.com/manteclaw/manteclaw-x402/blob/main/README.md>).
Payment signatures are off-chain; no ETH is needed to call.

## Source of truth

The live catalog: `https://manteclaw-x402.fly.dev/.well-known/agent-catalog.json`
(also served as `/.well-known/x402.json`). The service itself is at
<https://github.com/manteclaw/manteclaw-x402>. `info.version` (`5.0.0`)
tracks the service version reported by `GET /health`.

No secrets, keys, or credentials live in this repo — the only addresses in
the spec are the public USDC contract and the public payee wallet, both
already published by the live service.
