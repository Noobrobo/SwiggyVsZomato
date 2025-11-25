import pandas as pd
import ollama
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json
import re
import unicodedata
import sys
import logging
from logging import StreamHandler

# Attempt to import network exceptions (common with ollama's underlying http libraries)
try:
    from requests.exceptions import ConnectionError, Timeout as TimeoutError
except ImportError:
    from socket import timeout as TimeoutError
    from http.client import HTTPException as ConnectionError 

# --- A. CONFIGURATION & SETUP ---
# Use basic logging config: INFO level for general output, ERROR for critical issues
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)
logger = logging.getLogger(__name__)

# ===== CONFIGURATION (Should be externalized in prod) =====
INPUT_CSV = 'input.csv'
OUTPUT_CSV = 'output_with_themes.csv'
REVIEW_COLUMN = 'content'
MODEL_NAME = 'llama3.2'
MAX_WORKERS = 4
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
# =========================

# Column names for classification (Simplified for consistency)
CATEGORIES = [
    'Sentiment',
    'Delivery Experience',
    'Food / Order Quality',
    'App & System Issues',
    'Support / Resolution', # Renamed for simplicity
    'Price / Charges',
    'Positive Feedback',
    'Others'
]

# Standard empty result template
EMPTY_RESULT = {cat: '' for cat in CATEGORIES}

# EXPLICIT Classification guide for LLM, ensuring strict adherence to sub-themes
CLASSIFICATION_GUIDE = """THEME CATEGORIES (SIMPLIFIED):
A. DELIVERY EXPERIENCE (4 simple categories)
    - "Delivery Delay" (ANY delay: late, slow, took too long, hours)
    - "Rider Misbehavior" (Rider rude, unprofessional, bad behavior)
    - "Delivery Issue" (Wrong address, damaged package, partial delivery)
    - "Good Delivery Experience" (Fast, on-time, positive)
B. FOOD / ORDER QUALITY (4 simple categories)
    - "Poor Food Quality" (ANY quality issue: cold, stale, bad taste, spoiled, burnt)
    - "Wrong/Missing Items" (Wrong items, missing items, incorrect order)
    - "Poor Packaging" (Leaking, spilled, damaged packaging)
    - "Good Food Quality" (Tasty, fresh, hot, good quantity)
C. APP & SYSTEM ISSUES (5 simple categories)
    - "App Technical Issue" (ANY app problem: crash, lag, bug, freeze, slow)
    - "Payment Issue" (Payment failure, double charge, refund not processed)
    - "Order/Tracking Issue" (Can't place order, can't track, status wrong)
    - "Coupon/Promo Issue" (Coupon not working, discount not applied)
    - "Good App Experience" (App works well, smooth)
D. SUPPORT / RESOLUTION (3 simple categories)
    - "Poor Customer Support" (ANY support issue: not helpful, rude, slow, no response)
    - "Refund/Compensation Issue" (Refund not received, money not returned)
    - "Good Customer Support" (Helpful, quick response, issue resolved)
E. PRICE / CHARGES (4 simple categories)
    - "High Prices" (ANY price complaint: expensive, costly, high delivery charges)
    - "Hidden/Extra Charges" (Hidden fees, unexpected charges, GST, surge pricing)
    - "Billing Error" (Wrong amount, price mismatch, overcharged)
    - "Good Value" (Affordable, reasonable, worth money)
F. POSITIVE FEEDBACK (2 simple categories)
    - "Positive Experience" (ANY general praise: good, great, nice, best, love it)
    - "Specific Praise" (Explicitly praises delivery/food/app/value)
G. OTHERS
    - "Vague/Unclear" (Too vague to classify)
    - Use ONLY when review does NOT clearly fit any category above

CRITICAL RULES:
- Keep it SIMPLE - use the exact labels above
- DO NOT create new subcategories
- If unsure → Use "Others"
"""

def clean_review_text(text):
    """Clean review text to prevent JSON parsing issues"""
    if pd.isna(text) or not text:
        return ""
    
    text = str(text)
    text = unicodedata.normalize('NFKD', text)
    
    text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C' or char in '\n\r\t')
    
    # Replace problematic quotes
    text = text.replace('"', "'").replace('“', "'").replace('”', "'").replace('‘', "'").replace('’', "'")
    
    text = ' '.join(text.split())
    
    return text.strip()

def classify_review(review_id, review):
    """Classify review using hybrid approach with retry logic"""
    if pd.isna(review) or str(review).strip() == '':
        return EMPTY_RESULT.copy()
    
    review_clean = clean_review_text(review)
    if not review_clean:
        return EMPTY_RESULT.copy()
    
    review_lower = review_clean.lower()
    
    # Quick keyword fallback for single-word reviews
    words = review_lower.split()
    if len(words) == 1:
        word = words[0]
        if word in ['good', 'great', 'nice', 'excellent', 'amazing', 'awesome', 'super', 'best', 'perfect', 'wonderful', 'fantastic', 'badhiya', 'achha', 'mast']:
            result = EMPTY_RESULT.copy()
            result['Sentiment'] = 'Positive'
            result['Positive Feedback'] = 'Positive Experience'
            return result
        elif word in ['bad', 'worst', 'terrible', 'horrible', 'pathetic', 'bekar', 'kharab', 'bakwas']:
            result = EMPTY_RESULT.copy()
            result['Sentiment'] = 'Negative'
            return result
    
    # Use LLM with retries
    prompt = f"""{CLASSIFICATION_GUIDE}
REVIEW: "{review_clean}"
CRITICAL: 
1. The VALUE for each category KEY must be one of the **EXACT** sub-theme labels listed in the guide (e.g., "Delivery Delay" or "Poor Food Quality").
2. If the review does NOT contain the topic for a category, the VALUE **MUST** be an empty string ("").
3. Return **ONLY** the JSON object. NO explanations. NO extra text.
{{"Sentiment": "Positive/Negative/Neutral", "Delivery Experience": "", "Food / Order Quality": "", "App & System Issues": "", "Support / Resolution": "", "Price / Charges": "", "Positive Feedback": "", "Others": ""}}
"""
    result = EMPTY_RESULT.copy()
    
    for attempt in range(MAX_RETRIES):
        try:
            response = ollama.generate(
                model=MODEL_NAME,
                prompt=prompt,
                format='json',
                options={'temperature': 0.05, 'num_predict': 250}
            )

            result_text = response['response'].strip()
            parsed = json.loads(result_text)

            for key in CATEGORIES:
                result[key] = str(parsed.get(key, '')).strip()
            return result
            
        except json.JSONDecodeError as e:
            if attempt < MAX_RETRIES - 1:
                retry_delay = RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    f"ID {review_id} - JSON Parse Error (Attempt {attempt + 1}/{MAX_RETRIES}). Retrying in {retry_delay}s."
                )
                time.sleep(retry_delay)
                continue
                
            logger.error(
                f"ID {review_id} - JSON Decode Failure. Review: '{review_clean[:50]}...'. Error: {e}"
            )
            result['Sentiment'] = 'Negative'
            result['Others'] = 'JSON Parse Error'
            return result
        
        except (ConnectionError, TimeoutError) as e:
            if attempt < MAX_RETRIES - 1:
                retry_delay = RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    f"ID {review_id} - Network Error (Attempt {attempt + 1}/{MAX_RETRIES}). Retrying in {retry_delay}s."
                )
                time.sleep(retry_delay)
                continue
                
            logger.critical(
                f"ID {review_id} - Network Failure. Review: '{review_clean[:50]}...'. Error: {e.__class__.__name__}"
            )
            result['Sentiment'] = 'Error'
            result['Others'] = 'Network Error'
            return result
            
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                retry_delay = RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    f"ID {review_id} - Unexpected Error (Attempt {attempt + 1}/{MAX_RETRIES}). Retrying in {retry_delay}s. Error: {e.__class__.__name__}"
                )
                time.sleep(retry_delay)
                continue
                
            logger.critical(
                f"ID {review_id} - Fatal Error. Review: '{review_clean[:50]}...'. Error: {e.__class__.__name__}: {e}"
            )
            result['Sentiment'] = 'Error'
            result['Others'] = f'{e.__class__.__name__}'
            return result

    return EMPTY_RESULT.copy()

def process_batch(start_idx, reviews_list):
    """Process batch of reviews (thread-safe)"""
    results = []
    for i, review in enumerate(reviews_list):
        abs_idx = start_idx + i 
        classification = classify_review(abs_idx, review) 
        results.append((abs_idx, classification)) 
    return results

def main():
    logger.info("=" * 70)
    logger.info("Review Classification - Hybrid (Keywords + LLM)")
    logger.info("=" * 70)

    logger.info("\n[1/6] Checking Ollama...")
    try:
        ollama.list()
        logger.info("✓ Ollama running")
    except Exception:
        logger.critical("✗ Ollama connection failed. Start Ollama first: ollama serve")
        return

    logger.info(f"\n[2/6] Loading {INPUT_CSV}...")
    try:
        df = pd.read_csv(INPUT_CSV)
        logger.info(f"✓ Loaded {len(df)} rows")
    except Exception as e:
        logger.critical(f"✗ Error loading CSV: {e}")
        return

    if REVIEW_COLUMN not in df.columns:
        logger.critical(f"✗ Column '{REVIEW_COLUMN}' not found in CSV.")
        return

    logger.info(f"\n[3/6] Dataset: {len(df)} reviews")
    
    # In a true production environment, user input would be replaced by CLI arguments
    try:
        user_input = input("Process how many? (number or 'all'): ")
        if user_input.lower() == 'all':
            rows = len(df)
        else:
            rows = min(int(user_input), len(df))
    except:
        rows = min(100, len(df))
        
    df = df.head(rows).copy()
    logger.info(f"✓ Processing {len(df)} reviews")

    logger.info(f"\n[4/6] Classifying with {MAX_WORKERS} workers...")
    start_time = time.time()

    for cat in CATEGORIES:
        df[cat] = ''

    batch_size = max(1, len(df) // MAX_WORKERS)
    
    # Progress tracking variables
    total_reviews = len(df)
    completed = 0
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for i in range(0, total_reviews, batch_size):
            end = min(i + batch_size, total_reviews)
            reviews_slice = df.iloc[i:end][REVIEW_COLUMN].tolist()
            futures.append(executor.submit(process_batch, i, reviews_slice))

        for future in as_completed(futures):
            batch_results = future.result()
            for idx, cls in batch_results:
                for cat in CATEGORIES:
                    df.at[idx, cat] = cls.get(cat, '') # Use .get for robustness

            completed += len(batch_results)
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (total_reviews - completed) / rate / 60 if rate > 0 else 0
            
            logger.info(f"Progress: {completed}/{total_reviews} ({completed/total_reviews*100:.1f}%) | Rate: {rate:.2f}/sec | ETA: {eta:.1f} min")

    logger.info(f"\n[5/6] Complete! Total time: {(time.time()-start_time)/60:.1f} min")

    logger.info("\n[6/6] Summary:")
    logger.info("\nSentiment:")
    sentiment_counts = df['Sentiment'].value_counts(dropna=False)
    for s, c in sentiment_counts.items():
        s_display = 'N/A' if pd.isna(s) else s
        logger.info(f"  {s_display}: {c} ({c/len(df)*100:.1f}%)")

    logger.info("\nThemes:")
    theme_categories = [cat for cat in CATEGORIES if cat != 'Sentiment']
    for cat in theme_categories:
        n = df[cat].astype(str).str.strip().ne('').sum()
        if n > 0:
            logger.info(f"  {cat}: {n} ({n/len(df)*100:.1f}%)")

    logger.info(f"\nSaving to {OUTPUT_CSV}...")
    df.to_csv(OUTPUT_CSV, index=False)
    logger.info("✓ Saved!")

    logger.info("\n" + "=" * 70)
    logger.info("Done! Check output_with_themes.csv")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()