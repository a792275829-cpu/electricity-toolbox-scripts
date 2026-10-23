//! Display labels never replace the raw URLs used for WPS requests or storage.
use serde_json::{json, Value};
use std::collections::HashSet;

pub fn url_choices(payload: &Value) -> Result<Value, Box<dyn std::error::Error>> {
    let titles = payload.get("titles").and_then(Value::as_object);
    let label = |url: &str| match titles
        .and_then(|map| map.get(url))
        .and_then(Value::as_str)
        .map(str::trim)
    {
        Some(title) if !title.is_empty() => format!("{url}（{title}）"),
        _ => url.to_owned(),
    };
    let selected = payload
        .get("selected")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    // Resolve exact known labels only: parentheses can be part of a valid URL.
    let resolved = titles
        .and_then(|map| map.keys().find(|url| label(url) == selected))
        .map(String::as_str)
        .unwrap_or(selected);
    let mut seen = HashSet::new();
    let mut options = Vec::new();
    if let Some(urls) = payload.get("urls").and_then(Value::as_array) {
        for url in urls.iter().filter_map(Value::as_str).map(str::trim) {
            if !url.is_empty() && seen.insert(url.to_owned()) {
                options.push(json!({"url": url, "label": label(url)}));
            }
        }
    }
    Ok(json!({"protocol": 1, "options": options, "url": resolved, "label": label(resolved)}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titled_choices_keep_raw_urls_and_nested_parentheses() {
        let result = url_choices(&json!({
            "urls": ["https://www.kdocs.cn/l/one", "https://www.kdocs.cn/l/two", "https://www.kdocs.cn/l/one"],
            "titles": {"https://www.kdocs.cn/l/one": "出清情况（分公司2026.10.12）"},
            "selected": "https://www.kdocs.cn/l/one（出清情况（分公司2026.10.12））"
        })).unwrap();
        assert_eq!(result["url"], "https://www.kdocs.cn/l/one");
        assert_eq!(result["options"].as_array().unwrap().len(), 2);
        assert_eq!(result["options"][1]["label"], "https://www.kdocs.cn/l/two");
        assert_eq!(result["options"][0]["url"], result["url"]);
    }

    #[test]
    fn raw_urls_with_parentheses_are_never_truncated() {
        for url in [
            "https://example.test/（原始）?x=(a)",
            "https://www.kdocs.cn/l/new",
            "",
        ] {
            let result = url_choices(&json!({"selected": url, "titles": {}, "urls": []})).unwrap();
            assert_eq!(result["url"], url);
        }
    }
}
