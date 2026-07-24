-- Discount Tier Margin Analysis
-- Demonstrates the margin-destroying effect of high discounts

WITH discount_tiers AS (
    SELECT 
        CASE 
            WHEN discount = 0 THEN '0%'
            WHEN discount <= 0.20 THEN '1-20%'
            WHEN discount <= 0.40 THEN '21-40%'
            ELSE '41%+'
        END AS tier,
        discount,
        profit,
        sales,
        profit_margin,
        is_loss
    FROM superstore_sales
),
tier_summary AS (
    SELECT 
        tier,
        COUNT(*) AS order_count,
        ROUND(AVG(profit_margin), 2) AS avg_margin_pct,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY profit_margin), 2) AS median_margin_pct,
        ROUND(SUM(profit), 2) AS total_profit,
        ROUND(SUM(sales), 2) AS total_sales,
        ROUND(AVG(discount) * 100, 1) AS avg_discount_pct,
        ROUND(SUM(is_loss) * 100.0 / COUNT(*), 1) AS loss_rate_pct,
        ROUND(AVG(sales), 2) AS avg_sales_per_order
    FROM discount_tiers
    GROUP BY tier
)
SELECT 
    tier,
    order_count,
    ROUND(order_count * 100.0 / SUM(order_count) OVER (), 1) AS pct_of_orders,
    avg_margin_pct,
    median_margin_pct,
    total_profit,
    total_sales,
    avg_discount_pct,
    loss_rate_pct,
    avg_sales_per_order,
    CASE 
        WHEN avg_margin_pct < -50 THEN 'Critical - Stop Discounts'
        WHEN avg_margin_pct < 0 THEN 'Danger Zone'
        WHEN avg_margin_pct < 15 THEN 'Warning - Review Pricing'
        ELSE 'Healthy'
    END AS margin_health
FROM tier_summary
ORDER BY 
    CASE tier 
        WHEN '0%' THEN 1 
        WHEN '1-20%' THEN 2 
        WHEN '21-40%' THEN 3 
        WHEN '41%+' THEN 4 
    END;
