"""Create synthetic sample documents and load them."""
import os, core
D = {
 ("finance_report_2025.txt","public"): "Annual Finance Report\n\nRevenue 2024: $1,200,000\nRevenue 2025: $1,500,000\nNet income 2024: $120,000\nNet income 2025: $210,000\nExpenses 2025: $1,290,000\n",
 ("budget_memo.txt","public"): "Budget Memo\n\nRevenue 2025: $1,480,000 (preliminary figure, pre-audit)\n",
 ("hr_policy_2024.txt","public"): "HR Policy 2024\n\nAnnual leave: employees receive 20 days of annual leave per year.\n\nProbation period is 3 months.\n\nRemote work is allowed up to 2 days per week.\n",
 ("hr_handbook_2026.txt","public"): "Employee Handbook 2026\n\nAnnual leave: employees receive 25 days of annual leave per year.\n\nSick leave: 10 days per year.\n",
 ("salary_bands_confidential.txt","confidential"): "CONFIDENTIAL Salary Bands\n\nThe CEO salary is $400,000. Senior manager salary band is $120,000 to $150,000.\n",
}
for (n, c), t in D.items():
    open(f"sample_docs/{n}", "w").write(t); core.ingest(n, t, c)
print("Seeded", len(D), "documents")
