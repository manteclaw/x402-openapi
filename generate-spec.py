#!/usr/bin/env python3
"""Generate openapi.yaml for the Manteclaw x402 API service.

Reads the live v5 agent catalog (agent-catalog.json) — either from the live
service or from a local file — and emits an OpenAPI 3.0.3 spec covering all
200 endpoints: the 199 paid x402 endpoints plus the free /health probe.

Each paid operation carries an `x-payment-info` extension with the exact x402
v2 payment terms (scheme, network, asset, amount in USDC atomic units,
payee, timeout), so clients and discovery indexes can see the price without
making a call.

Usage:
    python3 generate-spec.py                       # fetch live catalog, write openapi.yaml
    python3 generate-spec.py --input catalog.json  # use a local catalog file
    python3 generate-spec.py --output spec.yaml    # write somewhere else

Requires PyYAML (pip install pyyaml). Standard library otherwise.
"""

import argparse
import json
import re
import sys
import urllib.request
from decimal import Decimal

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

CATALOG_URL = "https://manteclaw-x402.fly.dev/.well-known/agent-catalog.json"
SERVICE_VERSION = "5.0.0"  # tracks the live service (GET /health -> version "5.0")
USDC_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base
USDC_DECIMALS = 6
NETWORK = "eip155:8453"  # Base mainnet
FACILITATOR = "https://facilitator.payai.network"
MAX_TIMEOUT_SECONDS = 300

CATEGORY_DESCRIPTIONS = {
    "analytics": "Analytics",
    "chain": "Chain, blocks, transactions and gas",
    "defi": "DeFi yields, TVL and protocols",
    "dex": "DEX pairs, volume and trending",
    "gas": "Gas prices",
    "governance": "Governance and protocol votes",
    "lending": "Lending markets, rates and positions",
    "memes": "Memecoins and trending tokens",
    "naming": "Naming (ENS)",
    "nft": "NFTs",
    "oracles": "Price oracles and feeds",
    "prediction": "Prediction markets",
    "security": "Token security",
    "stablecoins": "Stablecoins, pegs and supply",
    "status": "Service status",
    "token": "Tokens",
    "utils": "Utilities",
    "wallet": "Wallets",
}

# Human descriptions for path parameters, by parameter name.
PARAM_DESCRIPTIONS = {
    "address": "An address on Base (0x-prefixed, 40 hex characters) — a token "
               "contract or a wallet, depending on the endpoint.",
    "amount": 'Amount to convert (decimal string, e.g. "1" or "1.5").',
    "chain": 'Chain slug (e.g. "base").',
    "coin": 'CoinGecko coin id (e.g. "ethereum").',
    "collection": "NFT collection contract address on Base (0x-prefixed, "
                  "40 hex characters).",
    "gas": "Gas units.",
    "gwei": "Gas price in gwei.",
    "hash": "Transaction hash (0x-prefixed, 64 hex characters).",
    "name": 'ENS name (e.g. "vitalik.eth").',
    "number": "Block number.",
    "owner": "Owner wallet address on Base (0x-prefixed, 40 hex characters).",
    "pair": "DEX pair contract address on Base (0x-prefixed, 40 hex characters).",
    "pool": "Liquidity pool contract address on Base (0x-prefixed, "
            "40 hex characters).",
    "protocol": 'Protocol slug (e.g. "aerodrome").',
    "query": "Search query.",
    "spender": "Spender wallet address on Base (0x-prefixed, 40 hex characters).",
    "text": "Text to hash.",
    "to": "Recipient wallet address on Base (0x-prefixed, 40 hex characters).",
    "tokenId": "NFT token ID.",
    "token_address": "ERC-20 token contract address on Base (0x-prefixed, "
                     "40 hex characters).",
    "wallet": "Wallet address on Base (0x-prefixed, 40 hex characters).",
}

ADDRESS_PARAMS = {
    "address", "token_address", "wallet", "owner", "spender", "to",
    "collection", "pair", "pool",
}
INTEGER_PARAMS = {"number", "gwei", "gas"}


def load_catalog(args):
    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            return json.load(fh), args.input
    req = urllib.request.Request(
        CATALOG_URL, headers={"User-Agent": "x402-openapi-generator/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp), CATALOG_URL


def operation_id(asset_id):
    """kebab-case id -> camelCase operationId, e.g. token-live-price -> tokenLivePrice."""
    parts = re.split(r"[-_]", asset_id)
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def param_schema(name):
    if name in ADDRESS_PARAMS:
        return {"type": "string", "pattern": "^0x[0-9a-fA-F]{40}$"}
    if name == "hash":
        return {"type": "string", "pattern": "^0x[0-9a-fA-F]{64}$"}
    if name in INTEGER_PARAMS:
        return {"type": "integer", "format": "int64", "minimum": 0}
    return {"type": "string"}


def examples_from_preview(asset):
    """Extract per-parameter example values from the catalog preview_endpoint."""
    endpoint, preview = asset["endpoint"], asset.get("preview_endpoint", "")
    if "{" not in endpoint or not preview or preview == endpoint:
        return {}
    e_parts, p_parts = endpoint.split("/"), preview.split("/")
    if len(e_parts) != len(p_parts):
        return {}
    examples = {}
    for e_seg, p_seg in zip(e_parts, p_parts):
        m = re.fullmatch(r"\{(\w+)\}", e_seg)
        if m:
            examples[m.group(1)] = p_seg
    return examples


def build_spec(catalog):
    assets = sorted(catalog["assets"], key=lambda a: a["endpoint"])
    payee = catalog["wallet_address"]
    base_url = catalog["base_url"]

    paths = {}
    tags = {}
    for asset in assets:
        category = asset["category"]
        tags.setdefault(category, CATEGORY_DESCRIPTIONS.get(category, category.title()))
        price = Decimal(asset["price_usdc"])
        paid = price > 0
        examples = examples_from_preview(asset)

        parameters = []
        for name in re.findall(r"\{(\w+)\}", asset["endpoint"]):
            param = {
                "name": name,
                "in": "path",
                "required": True,
                "description": PARAM_DESCRIPTIONS.get(
                    name, f"Value for the '{name}' path segment."
                ),
                "schema": param_schema(name),
            }
            if name in examples:
                example = examples[name]
                if param["schema"]["type"] == "integer":
                    try:
                        example = int(example)
                    except ValueError:
                        pass
                param["example"] = example
            parameters.append(param)

        operation = {
            "operationId": operation_id(asset["id"]),
            "summary": asset["name"],
            "description": asset["description"],
            "tags": [category],
            "responses": {
                "200": {
                    "description": "Successful response (JSON).",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ApiResponse"}
                        }
                    },
                },
                "400": {
                    "description": "Bad request — invalid parameter "
                                   "(e.g. malformed address).",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
                "429": {
                    "description": "Too many requests — an upstream data source "
                                   "rate-limited the call (e.g. CoinGecko free tier).",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
                "500": {
                    "description": "Upstream error — the service returns a clean "
                                   "error, never fabricated data.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
            },
        }
        if parameters:
            operation["parameters"] = parameters
        if paid:
            atomic = int(price * (10 ** USDC_DECIMALS))
            operation["x-payment-info"] = {
                "scheme": "exact",
                "network": NETWORK,
                "asset": USDC_ASSET,
                "amount": str(atomic),
                "payTo": payee,
                "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
                "priceUsdc": asset["price_usdc"],
            }
            operation["responses"]["402"] = {
                "description": (
                    "Payment Required — this endpoint is pay-per-call. The response "
                    "carries the payment terms in the base64-encoded "
                    "`payment-required` header (x402 v2). Sign an EIP-3009 "
                    "transferWithAuthorization for the amount in `x-payment-info` "
                    "and retry with the signature in the `X-PAYMENT` header. "
                    "Payment signatures are off-chain; no ETH is needed to call."
                ),
                "headers": {
                    "payment-required": {
                        "description": "Base64-encoded x402 v2 payment terms.",
                        "schema": {"type": "string"},
                    }
                },
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                    }
                },
            }
        paths[asset["endpoint"]] = {"get": operation}

    paid_count = sum(1 for a in assets if Decimal(a["price_usdc"]) > 0)
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Manteclaw x402 API Service",
            "description": (
                catalog.get("description", "")
                + "\n\n"
                + f"Pay-per-call API on Base: {paid_count} paid endpoints priced "
                + "in USDC per call via x402 v2, plus a free /health probe. "
                + "Unpaid calls return HTTP 402 with the payment terms in the "
                + "`payment-required` header; each paid operation documents its "
                + "exact price in `x-payment-info`. Data comes from live sources "
                + "(Base RPC, Dexscreener, DeFiLlama, CoinGecko, ENS, "
                + "Alternative.me) — no API keys, no subscriptions."
            ),
            "version": SERVICE_VERSION,
            "contact": {
                "name": "Manteclaw",
                "url": "https://github.com/manteclaw",
            },
        },
        "servers": [
            {
                "url": base_url,
                "description": "Production (Base mainnet, USDC)",
            }
        ],
        "externalDocs": {
            "description": "Service repository and how-to-call guide",
            "url": "https://github.com/manteclaw/manteclaw-x402",
        },
        "x-x402": {
            "facilitator": FACILITATOR,
            "network": NETWORK,
            "asset": USDC_ASSET,
            "payTo": payee,
        },
        "tags": [
            {"name": name, "description": desc}
            for name, desc in sorted(tags.items())
        ],
        "paths": paths,
        "components": {
            "schemas": {
                "ApiResponse": {
                    "type": "object",
                    "description": "Endpoint-specific JSON payload; shape varies "
                                   "per endpoint.",
                    "additionalProperties": True,
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "string",
                            "description": "Human-readable error message.",
                        }
                    },
                },
            }
        },
    }
    return spec, len(assets), paid_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="local catalog JSON file (default: fetch live)")
    parser.add_argument("--output", default="openapi.yaml",
                        help="output spec path (default: openapi.yaml)")
    args = parser.parse_args()

    catalog, source = load_catalog(args)
    spec, total, paid = build_spec(catalog)

    with open(args.output, "w", encoding="utf-8") as fh:
        yaml.safe_dump(spec, fh, sort_keys=False, allow_unicode=True, width=100)

    print(f"catalog source: {source}")
    print(f"endpoints: {total} total, {paid} paid, {total - paid} free")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
