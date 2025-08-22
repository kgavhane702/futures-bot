#!/usr/bin/env python3
"""
Strategy optimization script to find parameters for <5% drawdown with good win rate
"""

import os
import subprocess
import sys
from datetime import datetime

def run_backtest_with_params(params):
    """Run backtest with specific parameters"""
    # Set environment variables
    env = os.environ.copy()
    for key, value in params.items():
        env[key] = str(value)
    
    try:
        result = subprocess.run([sys.executable, 'backtest.py'], 
                              env=env, 
                              capture_output=True, 
                              text=True, 
                              timeout=300)
        
        if result.returncode == 0:
            # Parse the output to extract metrics
            output = result.stdout
            lines = output.split('\n')
            
            metrics = {}
            for line in lines:
                if 'trades:' in line:
                    metrics['trades'] = int(line.split(':')[1].strip())
                elif 'win_rate_%:' in line:
                    metrics['win_rate'] = float(line.split(':')[1].strip())
                elif 'expectancy_R:' in line:
                    metrics['expectancy'] = float(line.split(':')[1].strip())
                elif 'max_drawdown_%:' in line:
                    metrics['drawdown'] = float(line.split(':')[1].strip())
            
            return metrics
        else:
            return None
    except Exception as e:
        print(f"Error running backtest: {e}")
        return None

def test_parameter_combinations():
    """Test different parameter combinations"""
    
    # Base parameters from the working commit
    base_params = {
        'RSI_LONG_MIN': 52,
        'RSI_SHORT_MAX': 48,
        'MIN_ADX': 18,
        'ATR_MULT_SL': 2.0,
        'TP_R_MULT': 2.0,
        'MAX_POSITIONS': 3
    }
    
    # Parameter combinations to test for reducing drawdown
    test_combinations = [
        # More conservative RSI settings
        {**base_params, 'RSI_LONG_MIN': 55, 'RSI_SHORT_MAX': 45},
        {**base_params, 'RSI_LONG_MIN': 58, 'RSI_SHORT_MAX': 42},
        {**base_params, 'RSI_LONG_MIN': 60, 'RSI_SHORT_MAX': 40},
        
        # Higher ADX for stronger trends
        {**base_params, 'MIN_ADX': 20},
        {**base_params, 'MIN_ADX': 22},
        {**base_params, 'MIN_ADX': 25},
        
        # Tighter stop losses
        {**base_params, 'ATR_MULT_SL': 1.5},
        {**base_params, 'ATR_MULT_SL': 1.8},
        
        # Lower take profit for faster exits
        {**base_params, 'TP_R_MULT': 1.5},
        {**base_params, 'TP_R_MULT': 1.8},
        
        # Fewer positions
        {**base_params, 'MAX_POSITIONS': 2},
        {**base_params, 'MAX_POSITIONS': 1},
        
        # Combined conservative settings
        {**base_params, 'RSI_LONG_MIN': 58, 'RSI_SHORT_MAX': 42, 'MIN_ADX': 22, 'ATR_MULT_SL': 1.8, 'TP_R_MULT': 1.8, 'MAX_POSITIONS': 2},
        {**base_params, 'RSI_LONG_MIN': 60, 'RSI_SHORT_MAX': 40, 'MIN_ADX': 25, 'ATR_MULT_SL': 1.5, 'TP_R_MULT': 1.5, 'MAX_POSITIONS': 2},
    ]
    
    results = []
    
    print("🔍 Testing parameter combinations for <5% drawdown...")
    print("=" * 80)
    
    for i, params in enumerate(test_combinations):
        print(f"\nTest {i+1}/{len(test_combinations)}: {params}")
        
        metrics = run_backtest_with_params(params)
        
        if metrics:
            results.append({
                'params': params,
                'metrics': metrics
            })
            
            print(f"  Trades: {metrics.get('trades', 0)}")
            print(f"  Win Rate: {metrics.get('win_rate', 0):.2f}%")
            print(f"  Expectancy: {metrics.get('expectancy', 0):.3f}")
            print(f"  Drawdown: {metrics.get('drawdown', 0):.2f}%")
            
            # Check if this meets our criteria
            if (metrics.get('drawdown', 100) < 5.0 and 
                metrics.get('win_rate', 0) >= 50.0 and
                metrics.get('trades', 0) >= 10):
                print("  ✅ MEETS CRITERIA!")
        else:
            print("  ❌ Failed to run backtest")
    
    return results

def find_best_combination(results):
    """Find the best parameter combination"""
    
    # Filter results that meet our criteria
    valid_results = [
        r for r in results 
        if (r['metrics'].get('drawdown', 100) < 5.0 and 
            r['metrics'].get('win_rate', 0) >= 50.0 and
            r['metrics'].get('trades', 0) >= 10)
    ]
    
    if not valid_results:
        print("\n❌ No combinations found that meet the criteria")
        return None
    
    # Sort by expectancy (highest first)
    valid_results.sort(key=lambda x: x['metrics'].get('expectancy', 0), reverse=True)
    
    print("\n" + "=" * 80)
    print("🏆 BEST COMBINATIONS (Drawdown <5%, Win Rate ≥50%):")
    print("=" * 80)
    
    for i, result in enumerate(valid_results[:5]):
        params = result['params']
        metrics = result['metrics']
        
        print(f"\n{i+1}. Expectancy: {metrics.get('expectancy', 0):.3f}")
        print(f"   Parameters: {params}")
        print(f"   Trades: {metrics.get('trades', 0)}")
        print(f"   Win Rate: {metrics.get('win_rate', 0):.2f}%")
        print(f"   Drawdown: {metrics.get('drawdown', 0):.2f}%")
    
    return valid_results[0] if valid_results else None

def update_env_file(best_params):
    """Update .env file with the best parameters"""
    if not best_params:
        return
    
    env_content = f"""# Exchange Configuration
EXCHANGE=binanceusdm
API_KEY=your_api_key_here
API_SECRET=your_api_secret_here
USE_TESTNET=false
DRY_RUN=false

# Strategy Timeframes
TIMEFRAME=15m
HTF_TIMEFRAME=1h

# Universe Configuration
UNIVERSE_SIZE=12
MAX_POSITIONS={best_params['params']['MAX_POSITIONS']}

# Risk Management
ACCOUNT_EQUITY_USDT=100
RISK_PER_TRADE=0.01
LEVERAGE=3
MARGIN_MODE=isolated

# Strategy Parameters (Optimized for <5% Drawdown)
RSI_LONG_MIN={best_params['params']['RSI_LONG_MIN']}
RSI_SHORT_MAX={best_params['params']['RSI_SHORT_MAX']}
MIN_ADX={best_params['params']['MIN_ADX']}
ATR_MULT_SL={best_params['params']['ATR_MULT_SL']}
TP_R_MULT={best_params['params']['TP_R_MULT']}

# Performance: {best_params['metrics'].get('trades', 0)} trades, {best_params['metrics'].get('win_rate', 0):.1f}% win rate, {best_params['metrics'].get('drawdown', 0):.1f}% drawdown, {best_params['metrics'].get('expectancy', 0):.3f} expectancy
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print(f"\n✅ Updated .env file with optimized parameters")
    print(f"   Expected Performance:")
    print(f"   - Trades: {best_params['metrics'].get('trades', 0)}")
    print(f"   - Win Rate: {best_params['metrics'].get('win_rate', 0):.1f}%")
    print(f"   - Drawdown: {best_params['metrics'].get('drawdown', 0):.1f}%")
    print(f"   - Expectancy: {best_params['metrics'].get('expectancy', 0):.3f}")

if __name__ == "__main__":
    print("🚀 Strategy Optimization for <5% Drawdown")
    print("=" * 80)
    
    # Test parameter combinations
    results = test_parameter_combinations()
    
    # Find best combination
    best = find_best_combination(results)
    
    # Update .env file
    update_env_file(best)
    
    print("\n🎯 Optimization complete!")
