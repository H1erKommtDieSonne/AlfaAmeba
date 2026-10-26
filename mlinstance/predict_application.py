import sys
import json
import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler
from sklearn.impute import SimpleImputer

# Обязательное определение класса для корректной десериализации joblib
class FinancialPreprocessor:
    def __init__(self):
        self.num_cols = [
            'margin_current', 'operating_margin', 'gross_margin', 'operating_gap',
            'revenue', 'net_profit_current', 'operating_profit_current', 'total_assets', 'equity',
            'asset_turnover', 'current_ratio', 'cash_ratio', 'receivables_to_revenue', 'working_capital_to_assets',
            'debt_to_assets', 'equity_to_assets', 'negative_equity',
            'revenue_growth_1y', 'loss_streak'
        ]
        self.imputer = SimpleImputer(strategy='median', add_indicator=True)
        self.scaler = RobustScaler()
        self.top_okved_ = []
        self.imputer_feature_names_ = []

    def _engineer_features(self, data):
        df_out = data.copy()
        df_out.replace([np.inf, -np.inf], np.nan, inplace=True)
        
        df_out['asset_turnover'] = (df_out['revenue'] / df_out['total_assets']).replace([np.inf, -np.inf], np.nan)
        calc_margin = (df_out['net_profit_current'] / df_out['revenue']).replace([np.inf, -np.inf], np.nan)
        df_out['margin_current'] = df_out['margin_current'].fillna(calc_margin)
        
        calc_op = (df_out['operating_profit_current'] / df_out['revenue']).replace([np.inf, -np.inf], np.nan)
        df_out['operating_margin'] = df_out['operating_margin'].fillna(calc_op).fillna(df_out['margin_current'])
        df_out['operating_gap'] = df_out['margin_current'] - df_out['operating_margin']
        
        working_cap = df_out['current_assets'].fillna(0) - df_out['short_term_liabilities'].fillna(0)
        df_out['working_capital_to_assets'] = (working_cap / df_out['total_assets']).replace([np.inf, -np.inf], np.nan)
        
        for col in ['margin_current', 'operating_margin', 'gross_margin', 'operating_gap']:
            if col in df_out.columns:
                df_out[col] = df_out[col].clip(-1.0, 1.0)
                
        for col in ['revenue_growth_1y', 'loss_streak']:
            if col in df_out.columns:
                df_out[col] = df_out[col].fillna(0.0)
                
        if 'okved_group' in df_out.columns:
            df_out['okved_group'] = df_out['okved_group'].astype(str).str.strip().str[:2]
            df_out['okved_group'] = df_out['okved_group'].replace({'na': '99', 'nan': '99', 'No': '99', '': '99', 'None': '99'}).fillna('99')
            
        return df_out

    def transform(self, X):
        df_proc = self._engineer_features(X)
        if 'okved_group' in df_proc.columns:
            df_proc.loc[~df_proc['okved_group'].isin(self.top_okved_), 'okved_group'] = '99'
            okved_cat = df_proc['okved_group'].astype('category').values
        else:
            okved_cat = np.array(['99'] * len(df_proc), dtype='category')
            
        X_num = df_proc[self.num_cols]
        X_imp = self.imputer.transform(X_num)
        X_log = np.sign(X_imp) * np.log1p(np.abs(X_imp))
        X_scaled = self.scaler.transform(X_log)
        
        X_out = pd.DataFrame(X_scaled, columns=self.imputer_feature_names_)
        X_out['okved_group'] = okved_cat
        return X_out


# Загрузка обученного бандла моделей
BUNDLE_PATH = 'models/credit_risk_suite_full.joblib'
try:
    bundle = joblib.load(BUNDLE_PATH)
    prep = bundle['preprocessor']
    mods = bundle['models']
except FileNotFoundError:
    print(f"❌ Ошибка: файл модели '{BUNDLE_PATH}' не найден. Сначала выполните скрипт обучения.")
    sys.exit(1)


def score_company_dict(input_dict: dict) -> dict:
    """Принимает словарь с сырыми показателями отчётности и возвращает решение."""
    df_raw = pd.DataFrame([input_dict])
    
    # 1. Автоматический дорасчет недостающих финансовых коэффициентов
    rev = float(df_raw['revenue'].values[0])
    assets = float(df_raw['total_assets'].values[0])
    st_liab = float(df_raw['short_term_liabilities'].values[0])
    curr_assets = float(df_raw['current_assets'].values[0])
    equity = float(df_raw['equity'].values[0])
    
    if 'current_ratio' not in df_raw.columns or pd.isna(df_raw['current_ratio'].values[0]):
        df_raw['current_ratio'] = curr_assets / st_liab if st_liab > 0 else np.nan
    if 'cash_ratio' not in df_raw.columns or pd.isna(df_raw['cash_ratio'].values[0]):
        df_raw['cash_ratio'] = float(df_raw['cash'].values[0]) / st_liab if st_liab > 0 else np.nan
    if 'receivables_to_revenue' not in df_raw.columns or pd.isna(df_raw['receivables_to_revenue'].values[0]):
        df_raw['receivables_to_revenue'] = float(df_raw['receivables'].values[0]) / rev if rev > 0 else np.nan
    if 'debt_to_assets' not in df_raw.columns or pd.isna(df_raw['debt_to_assets'].values[0]):
        df_raw['debt_to_assets'] = st_liab / assets if assets > 0 else np.nan
    if 'equity_to_assets' not in df_raw.columns or pd.isna(df_raw['equity_to_assets'].values[0]):
        df_raw['equity_to_assets'] = equity / assets if assets > 0 else np.nan
    if 'negative_equity' not in df_raw.columns or pd.isna(df_raw['negative_equity'].values[0]):
        df_raw['negative_equity'] = 1 if equity < 0 else 0

    for col in ['margin_current', 'operating_margin', 'gross_margin', 'revenue_growth_1y', 'loss_streak']:
        if col not in df_raw.columns:
            df_raw[col] = np.nan

    # 2. Предобработка
    X_eval = prep.transform(df_raw)

    # 3. Прогнозы 5 моделей
    pred_margin = float(mods['net_margin'].predict(X_eval)[0] * 100)
    pred_rev = float(np.exp(mods['revenue'].predict(X_eval)[0]))
    prob_loss = float(mods['prob_loss'].predict_proba(X_eval)[0, 1] * 100)
    pred_op = float(mods['operating_margin'].predict(X_eval)[0] * 100)
    prob_liq = float(mods['prob_liquidity_crisis'].predict_proba(X_eval)[0, 1] * 100)

    # Лимит: 20% среднемесячной прогнозной выручки
    safe_limit = 0.20 * (pred_rev / 12)

    # Риск-правила
    if prob_loss > 40.0:
        verdict = "ОТКАЗ: Высокая вероятность убытка"
    elif prob_liq > 50.0:
        verdict = "ОТКАЗ: Высокий риск кассового разрыва (дефицит ликвидности)"
    elif pred_margin < 1.0:
        verdict = "ОДОБРЕНО С ДИСКОНТОМ 50%: Низкая маржинальность"
        safe_limit *= 0.5
    else:
        verdict = "✅ ОДОБРЕНО"

    return {
        "Прогноз выручки на год": f"{pred_rev:,.0f} тыс. руб.",
        "Безопасный кредитный лимит": f"{safe_limit:,.0f} тыс. руб.",
        "Прогноз чистой маржи": f"{pred_margin:+.2f}%",
        "Прогноз операционной маржи": f"{pred_op:+.2f}%",
        "Вероятность чистого убытка": f"{prob_loss:.1f}%",
        "Вероятность кассового разрыва (CR < 1)": f"{prob_liq:.1f}%",
        "Решение кредитного конвейера": verdict
    }


def interactive_mode():
    """Интерактивный ввод данных через консоль."""
    print("\n" + "="*60)
    print("ВВОД ДАННЫХ КРЕДИТНОЙ ЗАЯВКИ (в тысячах рублей)")
    print("="*60)
    
    def ask_float(prompt, default="0"):
        val = input(f"{prompt} [{default}]: ").strip()
        return float(val if val else default)

    company_data = {
        'revenue': ask_float("1. Годовая выручка (стр. 2110)", "50000"),
        'net_profit_current': ask_float("2. Чистая прибыль / убыток (стр. 2400)", "3000"),
        'operating_profit_current': ask_float("3. Прибыль от продаж (стр. 2200, если нет — жмите Enter)", "3500"),
        'total_assets': ask_float("4. Активы баланса всего (стр. 1600)", "40000"),
        'equity': ask_float("5. Капитал и резервы (стр. 1300)", "15000"),
        'current_assets': ask_float("6. Оборотные активы (стр. 1200)", "25000"),
        'short_term_liabilities': ask_float("7. Краткосрочные обязательства (стр. 1500)", "18000"),
        'cash': ask_float("8. Денежные средства (стр. 1250)", "2000"),
        'receivables': ask_float("9. Дебиторская задолженность (стр. 1230)", "12000"),
        'okved_group': input("10. Код отрасли ОКВЭД (первые 2 цифры, например 46, 47, 62) [46]: ").strip() or "46"
    }

    report = score_company_dict(company_data)
    print("\n" + "="*60)
    print("ИТОГОВОЕ ЗАКЛЮЧЕНИЕ КРЕДИТНОГО КОНВЕЙЕРА:")
    print("="*60)
    for k, v in report.items():
        print(f"  {k:38s}: {v}")
    print("="*60 + "\n")


if __name__ == "__main__":
    # Если в аргументах передан JSON-файл: python predict_application.py app.json
    if len(sys.argv) > 1 and sys.argv[1].endswith('.json'):
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            data = json.load(f)
        report = score_company_dict(data)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        # По умолчанию запускаем удобный опросник в терминале
        interactive_mode()