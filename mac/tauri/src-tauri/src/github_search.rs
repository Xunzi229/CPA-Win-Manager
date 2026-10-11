use std::collections::HashSet;
use std::time::Duration;

use reqwest::{Client, StatusCode};
use serde_json::{json, Value};

pub struct SearchResults {
    pub items: Vec<Value>,
    pub total: u64,
}

fn client(proxy: &str) -> Result<Client, String> {
    let mut builder = Client::builder()
        .user_agent("CPA-Mac-Manager/1.5.14")
        .timeout(Duration::from_secs(20))
        .connect_timeout(Duration::from_secs(10))
        .no_proxy();
    if !proxy.trim().is_empty() {
        let configured = reqwest::Proxy::all(proxy.trim())
            .map_err(|_| "代理地址无效，请检查设置中的代理配置。".to_string())?;
        builder = builder.proxy(configured);
    }
    builder
        .build()
        .map_err(|_| "无法初始化 GitHub 搜索连接。".to_string())
}

fn parse_results(payload: Value) -> Result<SearchResults, String> {
    let entries = payload
        .get("items")
        .and_then(Value::as_array)
        .ok_or("GitHub 搜索返回的数据格式无效。")?;
    let mut items = Vec::new();
    let mut seen = HashSet::new();
    for entry in entries {
        let Some(full_name) = entry.get("full_name").and_then(Value::as_str) else {
            continue;
        };
        let parts: Vec<_> = full_name.split('/').collect();
        if parts.len() != 2
            || parts.iter().any(|part| {
                part.is_empty()
                    || *part == "."
                    || *part == ".."
                    || !part.chars().all(|character| {
                        character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')
                    })
            })
            || !seen.insert(full_name.to_lowercase())
        {
            continue;
        }
        let description = entry
            .get("description")
            .and_then(Value::as_str)
            .filter(|text| !text.trim().is_empty())
            .unwrap_or("暂无项目说明");
        items.push(json!({
            "mode": "通用", "category": "GitHub", "name": parts[1],
            "description": description, "repository": format!("https://github.com/{full_name}")
        }));
    }
    let total = payload
        .get("total_count")
        .and_then(Value::as_u64)
        .unwrap_or(items.len() as u64);
    Ok(SearchResults { items, total })
}

fn parse_web_results(page: &str) -> Result<SearchResults, String> {
    let script = regex::Regex::new(
        r#"(?s)<script\b[^>]*\bdata-target=["']react-app\.embeddedData["'][^>]*>(.*?)</script>"#,
    )
    .unwrap();
    let embedded = script
        .captures(page)
        .and_then(|captures| captures.get(1))
        .ok_or("无法解析 GitHub 网页搜索结果，请稍后重试。")?;
    let data: Value = serde_json::from_str(embedded.as_str())
        .map_err(|_| "GitHub 网页搜索返回的数据格式无效。")?;
    let payload = data
        .get("payload")
        .ok_or("GitHub 网页搜索返回的数据格式无效。")?;
    let route = payload.get("blackbirdSearchRoute").unwrap_or(payload);
    if route.get("type").and_then(Value::as_str) != Some("repositories")
        || route
            .get("errors")
            .and_then(Value::as_array)
            .is_some_and(|errors| !errors.is_empty())
    {
        return Err("GitHub 网页搜索失败，请检查关键词后重试。".to_string());
    }
    let results = route
        .get("results")
        .and_then(Value::as_array)
        .ok_or("GitHub 网页搜索返回的数据格式无效。")?;
    let tags = regex::Regex::new(r"<[^>]*>").unwrap();
    let entries: Vec<_> = results
        .iter()
        .filter_map(|entry| {
            let repository = entry.pointer("/repo/repository")?;
            let owner = repository.get("owner_login")?.as_str()?;
            let name = repository.get("name")?.as_str()?;
            let description = tags
                .replace_all(
                    entry
                        .get("hl_trunc_description")
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                    "",
                )
                .replace("&quot;", "\"")
                .replace("&#39;", "'")
                .replace("&#x27;", "'")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&amp;", "&");
            Some(json!({"full_name": format!("{owner}/{name}"), "description": description}))
        })
        .collect();
    parse_results(json!({"items": entries, "total_count": route.get("result_count")}))
}

async fn fetch_web(client: &Client, endpoint: &str, query: &str) -> Result<SearchResults, String> {
    let response = client
        .get(endpoint)
        .query(&[("q", query), ("type", "repositories")])
        .header("Accept", "text/html")
        .send()
        .await
        .map_err(|_| "无法连接 GitHub 网页搜索，请检查网络或代理设置。".to_string())?;
    if !response.status().is_success() {
        return Err(format!(
            "GitHub 网页搜索失败（HTTP {}）。",
            response.status().as_u16()
        ));
    }
    let page = response
        .text()
        .await
        .map_err(|_| "无法读取 GitHub 网页搜索结果。".to_string())?;
    parse_web_results(&page)
}

async fn fetch(
    client: &Client,
    endpoint: &str,
    query: &str,
    fallback: Option<&str>,
) -> Result<SearchResults, String> {
    let response = client
        .get(endpoint)
        .query(&[("q", query), ("per_page", "30")])
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|error| {
            if error.is_timeout() {
                "GitHub 搜索超时，请检查网络或代理后重试。".to_string()
            } else {
                "无法连接 GitHub，请检查网络或代理设置。".to_string()
            }
        })?;
    match response.status() {
        StatusCode::FORBIDDEN | StatusCode::TOO_MANY_REQUESTS => {
            if let Some(endpoint) = fallback {
                return fetch_web(client, endpoint, query)
                    .await
                    .map_err(|message| format!("GitHub API 请求受限；{message}"));
            }
            return Err("GitHub 搜索请求受限，请稍后重试。".to_string());
        }
        status if !status.is_success() => {
            return Err(format!("GitHub 搜索失败（HTTP {}）。", status.as_u16()));
        }
        _ => {}
    }
    let payload = response
        .json::<Value>()
        .await
        .map_err(|_| "GitHub 搜索返回的数据格式无效。".to_string())?;
    parse_results(payload)
}

pub async fn search(query: &str, proxy: &str) -> Result<SearchResults, String> {
    let query = query.trim();
    if query.is_empty() {
        return Ok(SearchResults {
            items: Vec::new(),
            total: 0,
        });
    }
    if query.chars().count() > 256 {
        return Err("搜索关键词过长，请缩短后重试。".to_string());
    }
    fetch(
        &client(proxy)?,
        "https://api.github.com/search/repositories",
        query,
        Some("https://github.com/search"),
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    fn server(status: u16, body: &str) -> (String, std::thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!(
            "http://{}/search/repositories",
            listener.local_addr().unwrap()
        );
        let body = body.to_string();
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = Vec::new();
            loop {
                let mut buffer = [0; 4096];
                let count = stream.read(&mut buffer).unwrap();
                request.extend_from_slice(&buffer[..count]);
                if count == 0 || request.windows(4).any(|window| window == b"\r\n\r\n") {
                    break;
                }
            }
            write!(stream, "HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            String::from_utf8(request).unwrap()
        });
        (endpoint, handle)
    }

    #[test]
    fn parses_remote_repositories_and_deduplicates_addresses() {
        let results = parse_results(json!({"total_count": 123, "items": [
            {"full_name": "2dust/v2rayN", "description": "macOS client"},
            {"full_name": "2dust/v2rayn", "description": "duplicate"},
            {"full_name": "example/Tool", "description": null},
            {"full_name": "../invalid"}, {"full_name": "invalid"},
            {"full_name": "https://evil.example/tool"}, {"full_name": null}
        ]}))
        .unwrap();
        assert_eq!(results.items.len(), 2);
        assert_eq!(results.total, 123);
        assert_eq!(
            results.items[0]["repository"],
            "https://github.com/2dust/v2rayN"
        );
        assert_eq!(results.items[0]["mode"], "通用");
        assert_eq!(results.items[1]["description"], "暂无项目说明");
    }

    #[tokio::test]
    async fn sends_encoded_query_and_required_headers() {
        let (endpoint, server) = server(
            200,
            r#"{"items":[{"full_name":"2dust/v2rayN"}],"total_count":42}"#,
        );
        let results = fetch(&client("").unwrap(), &endpoint, "v2rayN in:name", None)
            .await
            .unwrap();
        let request = server.join().unwrap().to_lowercase();
        assert!(request.contains("q=v2rayn+in%3aname"));
        assert!(request.contains("per_page=30"));
        assert!(request.contains("user-agent: cpa-mac-manager/1.5.14"));
        assert!(request.contains("accept: application/vnd.github+json"));
        assert_eq!(results.items[0]["name"], "v2rayN");
        assert_eq!(results.total, 42);
    }

    #[tokio::test]
    async fn uses_the_configured_proxy_for_search() {
        let (proxy, server) = server(200, r#"{"items":[],"total_count":0}"#);
        let results = fetch(
            &client(&proxy).unwrap(),
            "http://github-search.invalid/search/repositories",
            "v2rayN",
            None,
        )
        .await
        .unwrap();
        let request = server.join().unwrap();
        assert!(request.starts_with("GET http://github-search.invalid/search/repositories?"));
        assert_eq!(results.total, 0);
    }

    #[tokio::test]
    async fn reports_http_errors_instead_of_empty_results() {
        for (status, message) in [(403, "受限"), (429, "受限"), (503, "HTTP 503")] {
            let (endpoint, server) = server(status, "{}");
            let error = fetch(&client("").unwrap(), &endpoint, "v2rayN", None)
                .await
                .err()
                .unwrap();
            server.join().unwrap();
            assert!(error.contains(message));
        }
    }

    #[tokio::test]
    async fn rejects_invalid_responses_and_limits_query_length() {
        for body in ["broken JSON", r#"{"message":"not search results"}"#] {
            let (endpoint, server) = server(200, body);
            let error = fetch(&client("").unwrap(), &endpoint, "v2rayN", None)
                .await
                .err()
                .unwrap();
            server.join().unwrap();
            assert!(error.contains("数据格式无效"));
        }
        assert!(search(&"查".repeat(257), "")
            .await
            .err()
            .unwrap()
            .contains("关键词过长"));
        assert!(search("  ", "").await.unwrap().items.is_empty());
    }

    #[test]
    fn handles_empty_results_and_missing_total_count() {
        assert_eq!(parse_results(json!({"items": []})).unwrap().total, 0);
        assert_eq!(
            parse_results(json!({"items": [{"full_name": "example/Tool"}]}))
                .unwrap()
                .total,
            1
        );
    }

    fn web_page(nested: bool) -> String {
        let route = json!({
            "type": "repositories", "result_count": 1360, "errors": [],
            "results": [{
                "repo": {"repository": {"owner_login": "2dust", "name": "v2rayN"}},
                "hl_trunc_description": "Windows &amp; macOS <em>client</em>"
            }]
        });
        let payload = if nested {
            json!({"blackbirdSearchRoute": route})
        } else {
            route
        };
        format!(
            r#"<script type="application/json" data-target="react-app.embeddedData">{}</script>"#,
            json!({"payload": payload})
        )
    }

    #[test]
    fn parses_current_and_previous_github_web_search_layouts() {
        for nested in [false, true] {
            let results = parse_web_results(&web_page(nested)).unwrap();
            assert_eq!(results.total, 1360);
            assert_eq!(
                results.items[0]["repository"],
                "https://github.com/2dust/v2rayN"
            );
            assert_eq!(results.items[0]["description"], "Windows & macOS client");
        }
        assert!(parse_web_results("<html>Sign in</html>").is_err());
        let empty = r#"<script data-target="react-app.embeddedData">{"payload":{"type":"repositories","results":[],"result_count":0}}</script>"#;
        assert_eq!(parse_web_results(empty).unwrap().total, 0);
        let error = r#"<script data-target="react-app.embeddedData">{"payload":{"type":"repositories","results":[],"errors":["invalid query"]}}</script>"#;
        assert!(parse_web_results(error).is_err());
    }

    #[tokio::test]
    async fn falls_back_to_web_search_when_the_api_is_rate_limited() {
        for status in [403, 429] {
            let (api, api_server) = server(status, "{}");
            let (web, web_server) = server(200, &web_page(true));
            let results = fetch(&client("").unwrap(), &api, "v2rayN", Some(&web))
                .await
                .unwrap();
            api_server.join().unwrap();
            let request = web_server.join().unwrap();
            assert!(request.contains("q=v2rayN&type=repositories"));
            assert!(request.contains("accept: text/html"));
            assert_eq!(results.items[0]["name"], "v2rayN");
            assert_eq!(results.total, 1360);
        }
    }

    #[tokio::test]
    async fn reports_errors_when_both_api_and_web_search_fail() {
        let (api, api_server) = server(403, "{}");
        let (web, web_server) = server(503, "unavailable");
        let error = fetch(&client("").unwrap(), &api, "v2rayN", Some(&web))
            .await
            .err()
            .unwrap();
        api_server.join().unwrap();
        web_server.join().unwrap();
        assert!(error.contains("API 请求受限"));
        assert!(error.contains("HTTP 503"));
    }
}
