"""
পে-স্লিপের Earnings / Deductions লাইন — বেতনের সারি (get_salary_summary_data এর এক সারি) থেকে।
মোট Earnings − মোট Deductions = Net (final_salary) সবসময় মেলে:
    Earnings  = Basic + সব allowance + OT + Bonus (+ rounding)
    Deductions = অনুপস্থিতি/কম কাজ + Late cut + Tax/PF + Loan EMI
"""
from decimal import Decimal

ZERO = Decimal(0)


def payslip_lines(row):
    s = row.get('structure') or {'basic': row['base_salary'], 'earnings': [], 'deductions': [],
                                 'prorated_gross': row['base_salary'], 'fixed_earnings': ZERO}
    ot = Decimal(row.get('overtime_pay') or 0)
    earnings = [('Basic salary', Decimal(s['basic']))] + [(n, Decimal(a)) for n, a, _p in s['earnings']]
    if ot:
        earnings.append(('Overtime', ot))
    if row.get('bonus_amount'):
        earnings.append(('Bonus', Decimal(row['bonus_amount'])))
    if row.get('rounding_adjustment'):
        earnings.append(('Rounding adjustment', Decimal(row['rounding_adjustment'])))

    worked_part = Decimal(row['earned_salary']) - ot - Decimal(s['fixed_earnings'])
    absence = max(Decimal(s['prorated_gross']) - worked_part, ZERO).quantize(Decimal('0.01'))
    deductions = []
    if absence:
        deductions.append((f"Absence / short hours ({row.get('absent_days', 0)} absent)", absence))
    if row.get('late_deduction'):
        deductions.append((f"Late deduction ({row.get('late_days', 0)} late → {row.get('late_deduction_days', 0)} day)",
                           Decimal(row['late_deduction'])))
    fixed = Decimal(row.get('fixed_deductions') or 0)
    listed = sum((Decimal(a) for _n, a in s['deductions']), ZERO)
    if fixed and fixed == listed:
        deductions += [(n, Decimal(a)) for n, a in s['deductions']]
    elif fixed:
        deductions.append(('Fixed deductions (capped to salary)', fixed))
    for i in row.get('loan_items') or []:
        if row.get('loan_deduction'):
            deductions.append((f"{i['loan'].get_loan_type_display()} EMI (left ৳{i['remaining']:,.0f})", Decimal(i['amount'])))
    if row.get('loan_shortfall') and row.get('loan_items') and row.get('loan_deduction'):
        # কিস্তি পুরো কাটা যায়নি — দেখানো লাইনগুলো আসল কাটার সমান করি
        shown = sum((Decimal(i['amount']) for i in row['loan_items']), ZERO)
        deductions.append(('EMI not deducted (salary too low)', -(shown - Decimal(row['loan_deduction']))))

    total_e = sum((a for _n, a in earnings), ZERO)
    total_d = sum((a for _n, a in deductions), ZERO)
    return {'earnings': earnings, 'deductions': deductions, 'total_earnings': total_e,
            'total_deductions': total_d, 'net': Decimal(row['final_salary'])}
