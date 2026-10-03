import requests
import sys

def fetch_github_stats(repo_path):
    # repo_path 格式: "owner/repo", 例如智谱的 "THUDM/ChatGLM3"
    url = f"https://api.github.com/repos/{repo_path}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            print(f"========== 【{data.get('name')}】 核心资产开源生态快照 ==========")
            print(f"所属机构/作者: {data.get('owner', {}).get('login')}")
            print(f"关注度 (Stars): {data.get('stargazers_count')} 🌟")
            print(f"真实衍生开发量 (Forks): {data.get('forks_count')} 🍴")
            print(f"技术债/活跃问题 (Open Issues): {data.get('open_issues_count')} ⚠️")
            print(f"开源商业化协议: {data.get('license', {}).get('name') if data.get('license') else '无/闭源'}")
            print(f"最后更新时间: {data.get('updated_at')}")
            
            # 买方量化判定逻辑
            stars = data.get('stargazers_count', 0)
            if stars > 10000:
                print("\n💡 投研判定: 属于绝对头部、具强共识的底层资产，具备极强护城河潜力。")
            elif stars < 1000:
                print("\n🚨 风险提示: 开发者基数极小，若研报鼓吹生态繁荣，请立即触发证伪逻辑！")
                
        else:
            print(f"⚠️ 无法获取仓库信息，状态码: {response.status_code}。请检查路径格式 (如 THUDM/ChatGLM3)。")
    except Exception as e:
        print(f"网络请求错误: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python github_repo_scraper.py <owner/repo>")
    else:
        fetch_github_stats(sys.argv[1])
