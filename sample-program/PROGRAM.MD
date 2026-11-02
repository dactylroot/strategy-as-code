# Retail Program

Three projects that ship one shopping experience. Run the UI against this manifest with
`PROGRAM_MANIFEST=sample-program/PROGRAM.MD`.

## Projects

| Key | Repo / Path | Planning Dir | Role | Depends On |
| --- | ----------- | ------------ | ---- | ---------- |
| storefront | .. | sample-program/storefront | Web store | platform |
| mobile | .. | sample-program/mobile | iOS and Android app | platform |
| platform | .. | sample-program/platform | Shared API and payments | |

## Initiatives

### Faster Checkout (Major)
- storefront:1.2.1
- mobile:1.2.1
- platform:1.2.1

### Work Offline (Minor)
- mobile:1.1.3
- platform:1.1.3
