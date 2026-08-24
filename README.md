# Data Chord

Data Chord is a data harmonization workflow application that helps data
curators turn CSV, TSV, or XLSX tabular data into reviewed, standardized
datasets. It recommends Bedrock Common Data Element (CDE) mappings and value
changes. The curator reviews those recommendations before export.

The final ZIP file contains the standardized data, the harmonization manifest,
and the column-mapping audit document.

## Choose your path

| Goal | Start here |
| --- | --- |
| Use an application that my organization hosts | [Use Data Chord](#use-data-chord) |
| Run a tagged version from source | [Run a tagged version from source](#run-a-tagged-version-from-source) |
| Change the application | [Develop Data Chord](#develop-data-chord) |
| Deploy or operate the application | [Deployment options](#deployment-options) |
| Understand the product or code structure | [Product guide](app.md) and [architecture guide](ARCHITECTURE.md) |

## Use Data Chord

You do not need to install Data Chord when your organization hosts it. Ask your
deployment owner for the application URL and access instructions.

The application guides you through five stages:

1. Upload a CSV, TSV, or XLSX file. For XLSX files, select the worksheet to use.
2. Review the recommended CDE mapping for each source column.
3. Run value harmonization with the confirmed mapping.
4. Review the results and apply manual value changes when needed.
5. Review the summary and download the final ZIP file.

See the [product guide](app.md) for the full workflow, output contents, and
behavior that affects review decisions.

## Run a tagged version from source

This option runs the hosted data profile on your computer. It is for licensed
users who have:

- Git, the [GitHub CLI](https://cli.github.com/), and
  [uv](https://docs.astral.sh/uv/);
- Python 3.13 or later;
- read access to `netrias/data_chord` and the private
  `netrias/agentic_harmonization` repository;
- AWS credentials that can use the required Amazon Bedrock models, read the
  reference-data table, and read and write the two cache tables; and
- one populated reference-data table and two DynamoDB cache tables. The cache
  tables can start empty.

Authenticate Git, clone the repository, and select a version from the
[repository tags](https://github.com/netrias/data_chord/tags):

```bash
gh auth status
gh auth setup-git
git clone https://github.com/netrias/data_chord.git
cd data_chord
git checkout vX.Y.Z
uv sync --frozen
```

Create the local configuration file. Set the AWS Region and the three DynamoDB
table names in `.env`:

```bash
cp .env.example .env
```

Start the application, then open <http://localhost:8000>:

```bash
uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

To update, fetch the tags, select another version, and install its locked
dependencies:

```bash
git fetch --tags
git checkout vX.Y.Z
uv sync --frozen
```

## Develop Data Chord

Development also needs [just](https://github.com/casey/just), Node.js 24.5 or
later, and npm 11.10 or later. Keep your development branch checked out instead
of a version tag.

From the repository root, install the locked Python and JavaScript dependencies
and create your local configuration:

```bash
just sync
cp .env.example .env
# Set the AWS Region and DynamoDB table names in .env.
just app-reload
```

Run `just --list` to see all shortcuts. These are the main development checks:

| Command | Scope |
| --- | --- |
| `just test` | Python tests |
| `just js-test` | JavaScript unit tests |
| `just lint` | Repository pre-commit checks |
| `just typecheck` | Python type check |
| `npm run typecheck` | JavaScript and TypeScript type check |
| `just test-e2e` | Local browser tests |
| `just infra-test` | OpenTofu and deployment-script tests |

Pull requests from forks run the infrastructure tests, JavaScript syntax check,
and JavaScript unit tests. GitHub does not give private dependency credentials
to forks. A maintainer must review an external change before running the Python,
type, lint, security, and browser checks on a branch in this repository.

### Measure browser performance

Run the local performance journey while you develop:

```bash
just perf-e2e
```

Run the deployed journey after the timing code is deployed and you are on the
company VPN:

```bash
just perf-staging
# Or use a specific URL.
just perf-staging https://your-staging-host.example.com
```

Set `PERF_REMOTE_ROWS=50` to change the generated CSV row count.

## Deployment options

Data Chord supports three deployment offers:

| Offer | Use this offer when | Data Chord provides |
| --- | --- | --- |
| Portable container | You own the container platform and want local SQLite reference data | One application image |
| Customer platform | You own the application platform and authentication | An AWS S3 and DynamoDB data plane |
| Full AWS | You want Data Chord to create the full application stack | ECS Fargate, load balancing, Cognito, storage, logs, and build resources |

The [deployment guide](DEPLOYMENT.md) contains the requirements and procedures
for all three offers. The [infrastructure guide](infra/README.md) defines AWS
resource ownership and deployment safety rules.
