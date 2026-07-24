-- Running Monthly Sales with YoY Comparison
-- Uses window functions for running totals and LAG for YoY

WITH monthly_sales AS (
    SELECT 
        DATE_TRUNC('month', order_date) AS month,
        SUM(sales) AS monthly_sales,
        SUM(profit) AS monthly_profit,
        COUNT(*) AS order_count,
        ROUND(SUM(profit) * 100.0 / NULLIF(SUM(sales), 0), 1) AS margin_pct
    FROM superstore_sales
    GROUP BY DATE_TRUNC('month', order_date)
),
monthly_with_running AS (
    SELECT 
        month,
        monthly_sales,
        monthly_profit,
        order_count,
        margin_pct,
        SUM(monthly_sales) OVER (ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_sales,
        SUM(monthly_profit) OVER (ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_profit,
        LAG(monthly_sales, 12) OVER (ORDER BY month) AS sales_12m_ago,
        ROUND(
            (monthly_sales - LAG(monthly_sales, 12) OVER (ORDER BY month)) 
            * 100.0 / NULLIF(LAG(monthly_sales, 12) OVER (ORDER BY month), 0), 
            1
        ) AS yoy_growth_pct
    FROM monthly_sales
)
SELECT 
    month,
    monthly_sales,
    monthly_profit,
    order_count,
    margin_pct,
    running_sales,
    running_profit,
    yoy_growth_pct,
    CASE 
        WHEN yoy_growth_pct > 15 THEN 'Strong Growth'
        WHEN yoy_growth_pct BETWEEN 0 AND 15 THEN 'Moderate Growth'
        WHEN yoy_growth_pct BETWEEN -15 AND 0 THEN 'Declining'
        ELSE 'Sharp Decline'
    END AS growth_trend
FROM monthly_with_running
ORDER BY month DESC;
