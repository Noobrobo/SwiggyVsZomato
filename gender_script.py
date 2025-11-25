import pandas as pd
import ollama
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ===== CONFIGURATION =====
INPUT_CSV = 'input.csv'  # Your downloaded CSV file
OUTPUT_CSV = 'output.csv'  # Results will be saved here
NAME_COLUMN = 'Name'  # Adjust this to match your column header (e.g., 'Username', 'Name', etc.)
MODEL_NAME = 'llama3.2'  # Ollama model to use
MAX_WORKERS = 4  # Number of parallel requests (adjust based on your CPU)
# =========================

def classify_gender(name):
    """
    Classify gender for a single name using local Ollama.
    Returns: 'male', 'female', 'unisex', or 'unknown'
    """
    if pd.isna(name) or str(name).strip() == '':
        return ''
    
    try:
        prompt = f'What is the gender of the name "{name}"? Reply with only one word: male, female, unisex, or unknown.'
        
        response = ollama.generate(
            model=MODEL_NAME,
            prompt=prompt,
            options={
                'temperature': 0.1,
                'num_predict': 10
            }
        )
        
        result = response['response'].strip().lower()
        
        # Extract gender from response
        if 'female' in result:
            return 'female'
        elif 'male' in result:
            return 'male'
        elif 'unisex' in result:
            return 'unisex'
        else:
            return 'unknown'
            
    except Exception as e:
        print(f"Error processing '{name}': {e}")
        return 'error'

def process_batch(df, start_idx, end_idx):
    """Process a batch of names and return results."""
    results = []
    for idx in range(start_idx, end_idx):
        name = df.iloc[idx][NAME_COLUMN]
        gender = classify_gender(name)
        results.append((idx, gender))
        
        # Progress update
        if (idx - start_idx + 1) % 10 == 0:
            print(f"  Processed {idx - start_idx + 1}/{end_idx - start_idx} in this batch")
    
    return results

def main():
    print("=" * 60)
    print("Gender Classification Script - Local Ollama")
    print("=" * 60)
    
    # Check if Ollama is running
    print("\n[1/5] Checking Ollama connection...")
    try:
        ollama.list()
        print("✓ Ollama is running")
    except Exception as e:
        print("✗ Error: Ollama is not running or not accessible")
        print(f"  Error details: {e}")
        print("\nPlease start Ollama by running: ollama serve")
        return
    
    # Load CSV
    print(f"\n[2/5] Loading CSV file: {INPUT_CSV}")
    try:
        df = pd.read_csv(INPUT_CSV)
        print(f"✓ Loaded {len(df)} rows")
        print(f"  Columns found: {list(df.columns)}")
    except FileNotFoundError:
        print(f"✗ Error: File '{INPUT_CSV}' not found")
        print(f"\nPlease ensure your CSV file is in the same folder as this script")
        print(f"Current columns expected: {NAME_COLUMN}")
        return
    except Exception as e:
        print(f"✗ Error loading CSV: {e}")
        return
    
    # Verify column exists
    if NAME_COLUMN not in df.columns:
        print(f"\n✗ Error: Column '{NAME_COLUMN}' not found in CSV")
        print(f"Available columns: {list(df.columns)}")
        print("\nPlease update NAME_COLUMN in the script to match your CSV")
        return
    
    # Check if Gender column already exists
    if 'Gender' in df.columns:
        print("\n! Warning: 'Gender' column already exists and will be overwritten")
        user_input = input("  Continue? (y/n): ")
        if user_input.lower() != 'y':
            print("Cancelled by user")
            return
    
    # Process names
    print(f"\n[3/5] Processing {len(df)} names using {MAX_WORKERS} parallel workers...")
    print(f"  Model: {MODEL_NAME}")
    print(f"  Estimated time: ~{len(df) * 2 // 60} minutes")
    
    start_time = time.time()
    
    # Create Gender column
    df['Gender'] = ''
    
    # Process in parallel batches
    batch_size = max(1, len(df) // MAX_WORKERS)
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        
        for i in range(0, len(df), batch_size):
            end_idx = min(i + batch_size, len(df))
            future = executor.submit(process_batch, df, i, end_idx)
            futures.append(future)
        
        # Collect results
        completed = 0
        for future in as_completed(futures):
            results = future.result()
            for idx, gender in results:
                df.at[idx, 'Gender'] = gender
            
            completed += len(results)
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            remaining = (len(df) - completed) / rate if rate > 0 else 0
            
            print(f"\nProgress: {completed}/{len(df)} ({completed/len(df)*100:.1f}%)")
            print(f"  Rate: {rate:.1f} names/sec | ETA: {remaining/60:.1f} min")
    
    elapsed_time = time.time() - start_time
    
    print(f"\n[4/5] Processing complete!")
    print(f"  Total time: {elapsed_time/60:.1f} minutes")
    print(f"  Average: {elapsed_time/len(df):.2f} seconds per name")
    
    # Show statistics
    print(f"\n[5/5] Results Summary:")
    gender_counts = df['Gender'].value_counts()
    for gender, count in gender_counts.items():
        print(f"  {gender}: {count} ({count/len(df)*100:.1f}%)")
    
    # Save to CSV
    print(f"\nSaving results to: {OUTPUT_CSV}")
    df.to_csv(OUTPUT_CSV, index=False)
    print("✓ File saved successfully!")
    
    print("\n" + "=" * 60)
    print("DONE! You can now upload the output.csv back to Google Sheets")
    print("=" * 60)

if __name__ == "__main__":
    main()