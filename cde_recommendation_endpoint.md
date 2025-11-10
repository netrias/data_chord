# CDE Recommendation Endpoint Documentation

## Overview

The **CDE Recommendation** endpoint analyzes a JSON representation of a tabular payload and returns, for each column, the most likely Common Data Element (CDE) targets along with similarity scores. Use this route when you need automated schema mapping or column‑to‑CDE suggestions before harmonization of the column values.

## HTTP Request

```
POST {BASE_URL}/v1/cde-recommendation
```

> **Production base URL**: `https://apiserver.netriasbdf.cloud`
> Replace `{BASE_URL}` with the appropriate environment.

## Required Headers

| Header         | Example            | Description                   |
| -------------- | ------------------ | ----------------------------- |
| `Content-Type` | `application/json` | Payload must be JSON‑encoded. |
| `x-api-key`    | `abcdef123456`     | Your issued API key.          |

## Request Body

Send a JSON object with a single top‑level key `body` whose value contains:

```jsonc
{
  "body": {
    "target_schema": "ccdi",          // REQUIRED – one of the supported schemas
    "data": {                                 // REQUIRED – column header → array of column values
      "donorAge":        [45, 60, 37],
      "ageMeasure":      ["years", null, null],
      "clinicalDiagnosis": ["Melanoma", "Basal Cell Carcinoma", "Benign Nevus"],
      ...
    }
  }
}
```

| Field           | Type     | Description                                                                                                                                         |
| --------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `target_schema` | `string` | Specifies which schema to query. Current valid values: `"sage_chipseq"`, `"sage_rnaseq"`, `"sage_imagingassay"`, `"gc"`, `"cds"`. |
| `data`          | `object` | Keys are **column names / headers**. Each value is an **array** of raw cell values (strings, numbers, or `null`). **Arrays need to be the same length.**         |

> **Note**: `null` or `None` values are ignored during similarity calculation.

## Successful Response (`200 OK`)

```json
{
  "statusCode": 200,
  "body": {
    "donorAge": [
      { "target": "age", "similarity": 0.9948 },
      { "target": "ageUnit", "similarity": -0.0013 },
      ...
    ],
    "clinicalDiagnosis": [
      { "target": "diagnosis", "similarity": 0.9418 },
      ...
    ],
    ...
  }
}
```

For each column key, up to **five** candidate objects are returned, ordered by descending `similarity`.

| Field        | Type     | Description                                                                                                                            |
| ------------ | -------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `target`     | `string` | Name of the suggested CDE within the selected schema.                                            |
| `similarity` | `float`  | Similarity between the column+values and the CDE definition. Values can be negative if the model believes the match is poor. |

## Error Responses

Same format as the **Harmonize** endpoint. The most common causes are:

| HTTP status       | Scenario                                            |
| ----------------- | --------------------------------------------------- |
| `400 Bad Request` | `target_schema` missing/invalid or `data` is empty. |
| `404 Not Found`   | Unknown `target_schema`.                            |

## Example – Python

```python
import requests, json

url = "https://apiserver.netriasbdf.cloud/v1/cde-recommendation"
headers = {
    "Content-Type": "application/json",
    "x-api-key": "<YOUR_API_KEY>"
}
my_column_data = {
    "donorAge": [45, 60, 37],
    "ageMeasure":      ["years", null, null],
    "clinicalDiagnosis": ["Melanoma", "Basal Cell Carcinoma", "Benign Nevus"],
    ...
}
payload = {
    "body": json.dumps({
        "target_schema": "ccdi",
        "data": my_column_data
    })
}
resp = requests.post(url, headers=headers, json=payload)
print(resp.json())
```

## Changelog

* 2025-07-08 – Initial draft.

