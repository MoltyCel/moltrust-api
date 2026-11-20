# AWS Marketplace listing — prod-k35yywhyj4eqg

Checked 2026-11-20 (AWS-X402 v2, Teil A 1).

| Fact | Value | How |
|---|---|---|
| Product ID | `prod-k35yywhyj4eqg` | embedded `productId` on the public listing page |
| Listing page | https://aws.amazon.com/marketplace/pp/prodview-baivobboznn4q | HTTP 200, product data present |
| Name | MolTrust - Agent Identity and Article 12 Audit Evidence | listing page |
| Seller profile | `seller-fqe6emkj6loqe`, display name MolTrust | listing page |
| Pricing | `FREE` | listing page `pricingModel` |
| Visibility | public (page renders for an anonymous fetch) | listing page |
| Product code (fulfillment) | `74az0btybm649octamy0sktos` | `app/aws_marketplace.py` |
| Catalog status | not readable | IAM user `moltrust-mp-fulfillment` lacks `aws-marketplace:DescribeEntity` |

`https://aws.amazon.com/marketplace/pp/prodview-k35yywhyj4eqg` is not a listing
URL (product ID in place of the listing ID) and shows no product. The website
badge on moltrust.ch/pricing.html links `prodview-baivobboznn4q`, which is
correct.

An x402 (Agentic Commerce) product cannot be added to this listing; it has to
be a second product (docs.aws.amazon.com/marketplace/latest/userguide/
agentic-commerce-products.html). This one stays free and unchanged.
