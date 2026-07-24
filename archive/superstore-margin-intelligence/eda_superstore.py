"""
eda_superstore.py
Exploratory Data Analysis on the real-world "Sample Superstore" retail
dataset (9,994 orders, US retail chain, 2014-2017).
Covers: data quality audit, cleaning, EDA, and business insights.
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="whitegrid")
pd.set_option("display.width", 120)

df = pd.read_csv("Sample_-_Superstore.csv", encoding="latin1")
raw_rows = len(df)

report = []
def log(line=""):
    print(line)
    report.append(str(line))

log("=" * 70)
log("STEP 1: DATA QUALITY AUDIT")
log("=" * 70)
log(f"Raw rows loaded: {raw_rows}")

missing = df.isna().sum()
missing = missing[missing > 0]
log(f"Columns with missing values: {'none found' if missing.empty else missing.to_dict()}")

full_dupes = df.duplicated().sum()
line_dupes = df.duplicated(subset=["Order ID", "Product ID"]).sum()
log(f"Fully duplicate rows: {full_dupes}")
log(f"Duplicate Order ID + Product ID combinations (possible double-entry): {line_dupes}")

neg_profit = (df["Profit"] < 0).sum()
neg_profit_pct = neg_profit / raw_rows * 100
log(f"Orders sold at a loss (negative profit): {neg_profit} ({neg_profit_pct:.1f}% of all orders)")

q1, q3 = df["Sales"].quantile([0.25, 0.75])
iqr = q3 - q1
upper_bound = q3 + 1.5 * iqr
sales_outliers = (df["Sales"] > upper_bound).sum()
log(f"Sales outliers beyond IQR upper bound ({upper_bound:,.0f}): {sales_outliers}")

log()
log("=" * 70)
log("STEP 2: CLEANING")
log("=" * 70)

before = len(df)
df = df.drop_duplicates(subset=["Order ID", "Product ID"]).reset_index(drop=True)
log(f"Removed {before - len(df)} duplicate order-line records")

df["Order Date"] = pd.to_datetime(df["Order Date"], format="%m/%d/%Y")
df["Ship Date"] = pd.to_datetime(df["Ship Date"], format="%m/%d/%Y")
df["ShippingDelayDays"] = (df["Ship Date"] - df["Order Date"]).dt.days
log(f"Parsed Order Date / Ship Date and derived ShippingDelayDays "
    f"(range: {df['ShippingDelayDays'].min()}-{df['ShippingDelayDays'].max()} days)")

df["ProfitMargin"] = np.where(df["Sales"] != 0, df["Profit"] / df["Sales"] * 100, 0)

clean_rows = len(df)
log(f"Final clean dataset: {clean_rows} rows (from {raw_rows} raw rows)")
df.to_csv("superstore_clean.csv", index=False)

log()
log("=" * 70)
log("STEP 3: EXPLORATORY ANALYSIS")
log("=" * 70)

total_sales = df["Sales"].sum()
total_profit = df["Profit"].sum()
overall_margin = total_profit / total_sales * 100
log(f"Total Sales: ${total_sales:,.0f}")
log(f"Total Profit: ${total_profit:,.0f}")
log(f"Overall Profit Margin: {overall_margin:.1f}%")
log(f"Total Orders analyzed: {df['Order ID'].nunique():,} unique orders / {len(df):,} line items")
log(f"Date range: {df['Order Date'].min().date()} to {df['Order Date'].max().date()}")

# Category performance
cat_perf = df.groupby("Category").agg(
    sales=("Sales", "sum"), profit=("Profit", "sum"), orders=("Order ID", "nunique")
).sort_values("sales", ascending=False)
cat_perf["margin_pct"] = (cat_perf["profit"] / cat_perf["sales"] * 100).round(1)
cat_perf["sales_share_pct"] = (cat_perf["sales"] / cat_perf["sales"].sum() * 100).round(1)
log(f"\nCategory performance:\n{cat_perf.round(0).to_string()}")

# Sub-category performance (find worst money-losers)
subcat_perf = df.groupby("Sub-Category").agg(
    sales=("Sales", "sum"), profit=("Profit", "sum")
).sort_values("profit")
subcat_perf["margin_pct"] = (subcat_perf["profit"] / subcat_perf["sales"] * 100).round(1)
log(f"\nSub-categories ranked by profit (lowest first):\n{subcat_perf.round(0).to_string()}")
worst_subcats = subcat_perf.head(3)
loss_making = subcat_perf[subcat_perf["profit"] < 0]
log(f"\nSub-categories operating at a net loss: {list(loss_making.index)}")

# Regional performance
region_perf = df.groupby("Region").agg(sales=("Sales", "sum"), profit=("Profit", "sum")).sort_values("sales", ascending=False)
region_perf["margin_pct"] = (region_perf["profit"] / region_perf["sales"] * 100).round(1)
log(f"\nRegional performance:\n{region_perf.round(0).to_string()}")

# Segment performance
segment_perf = df.groupby("Segment").agg(sales=("Sales", "sum"), profit=("Profit", "sum")).sort_values("sales", ascending=False)
log(f"\nCustomer segment performance:\n{segment_perf.round(0).to_string()}")

# Discount vs margin
disc_bins = pd.cut(df["Discount"], bins=[-0.01, 0, 0.2, 0.4, 1.0], labels=["0%", "1-20%", "21-40%", "41%+"])
disc_margin = df.groupby(disc_bins, observed=True).apply(
    lambda g: g["Profit"].sum() / g["Sales"].sum() * 100 if g["Sales"].sum() else 0
)
log(f"\nProfit margin by discount band:\n{disc_margin.round(1).to_string()}")

# Monthly trend
df["YearMonth"] = df["Order Date"].dt.to_period("M")
monthly = df.groupby("YearMonth")["Sales"].sum()
log(f"\nPeak sales month: {monthly.idxmax()} (${monthly.max():,.0f})")
log(f"Lowest sales month: {monthly.idxmin()} (${monthly.min():,.0f})")

# Shipping mode
ship_perf = df.groupby("Ship Mode")["ShippingDelayDays"].mean().sort_values()
log(f"\nAverage shipping delay by Ship Mode (days):\n{ship_perf.round(1).to_string()}")

log()
log("=" * 70)
log("STEP 4: KEY BUSINESS INSIGHTS")
log("=" * 70)
top_cat = cat_perf.index[0]
worst_margin_cat = cat_perf["margin_pct"].idxmin()
log(f"1. {top_cat} generates the most revenue (${cat_perf['sales'].iloc[0]:,.0f}, "
    f"{cat_perf['sales_share_pct'].iloc[0]:.1f}% of total sales).")
log(f"2. '{worst_margin_cat}' has the weakest category margin ({cat_perf.loc[worst_margin_cat,'margin_pct']:.1f}%), "
    f"dragged down by loss-making sub-categories: {list(loss_making.index)}.")
log(f"3. Discounts above 20% flip several segments unprofitable — margin falls from "
    f"{disc_margin.iloc[0]:.1f}% at 0% discount to {disc_margin.iloc[-1]:.1f}% at 41%+ discount.")
log(f"4. {region_perf.index[0]} is the top-performing region by sales (${region_perf['sales'].iloc[0]:,.0f}), "
    f"while {region_perf['margin_pct'].idxmin()} has the thinnest margin ({region_perf['margin_pct'].min():.1f}%).")
log(f"5. {segment_perf.index[0]} is the leading customer segment, contributing "
    f"${segment_perf['sales'].iloc[0]:,.0f} in sales.")
log(f"6. Same Day shipping has the shortest average delay ({ship_perf.min():.1f} days) vs "
    f"Standard Class ({ship_perf.max():.1f} days).")

with open("eda_findings_superstore.txt", "w") as f:
    f.write("\n".join(report))

# ---------------- VISUALS ----------------
fig, axes = plt.subplots(2, 3, figsize=(18, 10))

sns.barplot(x=cat_perf["sales"], y=cat_perf.index, ax=axes[0, 0], hue=cat_perf.index, palette="viridis", legend=False)
axes[0, 0].set_title("Sales by Category")
axes[0, 0].set_xlabel("Sales ($)")

subcat_sorted = subcat_perf.sort_values("profit")
colors = ["#C0392B" if v < 0 else "#2E7D32" for v in subcat_sorted["profit"]]
axes[0, 1].barh(subcat_sorted.index, subcat_sorted["profit"], color=colors)
axes[0, 1].set_title("Profit by Sub-Category (red = loss-making)")
axes[0, 1].set_xlabel("Profit ($)")
axes[0, 1].tick_params(axis="y", labelsize=8)

monthly_str = monthly.copy()
monthly_str.index = monthly_str.index.astype(str)
axes[0, 2].plot(monthly_str.index, monthly_str.values, marker="o", color="#1F2A44", linewidth=1)
axes[0, 2].set_title("Monthly Sales Trend")
axes[0, 2].tick_params(axis="x", rotation=90, labelsize=6)

sns.barplot(x=disc_margin.values, y=disc_margin.index.astype(str), ax=axes[1, 0], hue=disc_margin.index.astype(str), palette="magma", legend=False)
axes[1, 0].set_title("Profit Margin % by Discount Band")
axes[1, 0].set_xlabel("Profit Margin (%)")

sns.barplot(x=region_perf["sales"], y=region_perf.index, ax=axes[1, 1], hue=region_perf.index, palette="crest", legend=False)
axes[1, 1].set_title("Sales by Region")
axes[1, 1].set_xlabel("Sales ($)")

sns.boxplot(x="Category", y="Sales", data=df[df["Sales"] < upper_bound * 3], ax=axes[1, 2], hue="Category", palette="Set2", legend=False)
axes[1, 2].set_title("Sales Distribution by Category")
axes[1, 2].tick_params(axis="x", rotation=20)

plt.tight_layout()
plt.savefig("superstore_dashboard.png", dpi=150)
print("\nSaved superstore_dashboard.png and eda_findings_superstore.txt")
