"""一键运行 STGNN demo：合成数据 -> 时空图 -> 信用分 + 评估 + 解释。

用法：
    .venv\\Scripts\\python.exe run_demo.py
"""

from src.model.train_demo import train


def main():
    print("=" * 56)
    print(" 中小科技企业信用智能评估 — STGNN 最小可运行 Demo")
    print("=" * 56)
    model, metrics, samples, importance, ckpt = train()
    print("\n--- 样例企业评分（前 8 个抽样）---")
    for s in samples:
        flag = "违约" if s["true_default"] else "正常"
        print(
            f"  company {s['company_id']:>3}  PD={s['pd']:.3f}  "
            f"信用分={s['credit_score']:5.1f}  真实={flag}"
        )
    top = sorted(range(len(importance)), key=lambda i: importance[i], reverse=True)[:5]
    print("\n--- 最重要的 5 个特征(维度) ---")
    for i in top:
        print(f"  f{i:<2} importance={importance[i]:+.3f}")
    print(f"\ncheckpoint -> {ckpt}")
    print("outputs 目录: outputs/demo/ (sample_scores.json, feature_importance.json, .png)")


if __name__ == "__main__":
    main()
