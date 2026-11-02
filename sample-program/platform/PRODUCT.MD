# platform - Product Overview

## Summary
The shared API, payments, and inventory services behind the store and the app.

## Features

### 1. Services

#### 1.1 Catalog API

| WBS | Feature | Status | Value | Effort | Notes | Flag |
| --- | ------- | ------ | ----- | ------ | ----- | ---- |
| 1.1.1 | Catalog read API | Released | 10 | 4 |  |  |
| 1.1.2 | Faceted search index | Released | 8 | 6 |  |  |
| 1.1.3 | Bulk import | Live | 5 | 5 |  |  |

#### 1.2 Payments

| WBS | Feature | Status | Value | Effort | Notes | Flag |
| --- | ------- | ------ | ----- | ------ | ----- | ---- |
| 1.2.1 | Payment token vault | In-Progress | 9 | 8 | Blocks saved payment methods on both clients | gap |
| 1.2.2 | Refund workflow | Idea | 6 | 6 |  |  |

### 2. Operations

#### 2.1 Reliability

| WBS | Feature | Status | Value | Effort | Notes | Flag |
| --- | ------- | ------ | ----- | ------ | ----- | ---- |
| 2.1.1 | Rate limiting | Released | 7 | 3 |  |  |
| 2.1.2 | Order event stream | Live | 8 | 5 | Feeds push updates and email |  |
| 2.1.3 | Regional failover | Idea | 6 | 8 |  |  |
