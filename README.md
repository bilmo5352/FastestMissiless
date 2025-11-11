# Product Extraction Crawler

A production-ready web crawler for extracting product data from e-commerce websites and storing it in Supabase.

## Features

- **Universal Product Extraction**: Works with any e-commerce platform
- **Multiple Extraction Methods**: JSON-LD, inline JSON, API endpoints, and HTML parsing
- **Browser Rendering**: Handles JavaScript-heavy sites with Crawl4AI
- **Database Integration**: Stores products in Supabase
- **Concurrent Processing**: Processes multiple URLs in parallel
- **Production Ready**: Configured for Railway cloud deployment

## Prerequisites

- Python 3.9+
- Supabase account and project
- Railway account (for cloud deployment)

## Installation

1. Clone the repository:
```bash
git clone <your-repo-url>
cd NewMissile
```

2. Create a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file (or set environment variables in Railway):

```env
# Supabase Configuration (REQUIRED)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key-here

# Application Configuration (Optional)
SAVE_HTML_FILES=false  # Set to true to save HTML files locally
GLOBAL_CONCURRENCY=16
HEAVY_CONCURRENCY=4
PER_DOMAIN_LIMIT=3
HTTP_TIMEOUT=20
PAGE_TIMEOUT_MS=45000
DELAY_AFTER_WAIT=2.0

# Product Extraction Configuration
EXTRACT_PRODUCTS=true
MAX_PRODUCTS_PER_PAGE=50
```

## Usage

### Database Mode (Production)

Process URLs from the `product_page_urls` table:

```bash
python final.py --db --batch-size 100
```

Options:
- `--db` or `--database`: Enable database mode
- `--batch-size <number>`: Number of URLs to process per batch (default: 100)
- `--max-batches <number>`: Maximum number of batches to process (optional)

### Command-Line Mode (Testing)

Process URLs from command line:

```bash
python final.py https://example.com/products
```

Or from a file:

```bash
python final.py urls.txt
```

## Railway Deployment

### Step 1: Prepare Your Repository

1. Ensure all files are committed:
   - `final.py`
   - `requirements.txt`
   - `Procfile`
   - `railway.json`
   - `.gitignore`

2. Push to GitHub/GitLab

### Step 2: Deploy to Railway

1. Go to [Railway](https://railway.app)
2. Click "New Project"
3. Select "Deploy from GitHub repo"
4. Select your repository
5. Railway will automatically detect the `Procfile` and deploy

### Step 3: Configure Environment Variables

In Railway dashboard:

1. Go to your project → Variables
2. Add the following required variables:
   - `SUPABASE_URL`: Your Supabase project URL
   - `SUPABASE_KEY`: Your Supabase anon key
3. Optionally configure:
   - `SAVE_HTML_FILES=false` (recommended for production)
   - `GLOBAL_CONCURRENCY=16`
   - `HEAVY_CONCURRENCY=4`
   - `BATCH_SIZE=100`

### Step 4: Start Processing

The worker will automatically start processing URLs from the `product_page_urls` table when deployed.

## Database Schema

### `product_page_urls` Table

- `id`: Primary key
- `product_page_url`: URL to process
- `product_type_id`: Product type identifier
- `processing_status`: Status (pending, processing, completed, failed)
- `success`: Boolean indicating success
- `products_found`: Number of products found
- `products_saved`: Number of products saved
- `error_message`: Error message if failed
- `processed_at`: Timestamp of processing
- `retry_count`: Number of retry attempts

### `r_product_data` Table

- `id`: Primary key
- `product_page_url_id`: Foreign key to `product_page_urls`
- `product_type_id`: Product type identifier
- `product_name`: Product name
- `product_price`: Product price
- `product_image_url`: Product image URL
- `product_url`: Product detail page URL
- `product_description`: Product description
- `created_at`: Timestamp of creation

## Monitoring

Check processing status:

```sql
SELECT 
  processing_status,
  COUNT(*) as count
FROM product_page_urls
GROUP BY processing_status;
```

View recent completions:

```sql
SELECT 
  id,
  product_page_url,
  products_found,
  products_saved,
  processed_at
FROM product_page_urls
WHERE processing_status = 'completed'
ORDER BY processed_at DESC
LIMIT 10;
```

## Troubleshooting

### Common Issues

1. **No products extracted**: Check if the site requires JavaScript rendering
2. **Timeout errors**: Increase `PAGE_TIMEOUT_MS` in environment variables
3. **Database connection errors**: Verify `SUPABASE_URL` and `SUPABASE_KEY`
4. **Memory issues**: Reduce `GLOBAL_CONCURRENCY` and `HEAVY_CONCURRENCY`

### Logs

View Railway logs:
```bash
railway logs
```

Or in Railway dashboard: Project → Deployments → View Logs

## Performance Tuning

Adjust these environment variables based on your needs:

- **High throughput**: Increase `GLOBAL_CONCURRENCY` (16-32)
- **Memory constrained**: Reduce `HEAVY_CONCURRENCY` (2-4)
- **Slow sites**: Increase `PAGE_TIMEOUT_MS` (60000+)
- **Rate limiting**: Reduce `PER_DOMAIN_LIMIT` (1-2)

## License

[Your License Here]

