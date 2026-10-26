//! Dated market input validation and field-wise website/manual merging.
use serde_json::{json, Map, Value};

struct Field {
    key: &'static str,
    name: &'static str,
    day: &'static str,
    realtime: &'static str,
    unit: &'static str,
}

const FIELDS: [Field; 9] = [
    Field {
        key: "load",
        name: "统调负荷峰值",
        day: "统调负荷峰值",
        realtime: "统调实际负荷峰值",
        unit: "万千瓦",
    },
    Field {
        key: "aclass",
        name: "省内A类电源最大出力",
        day: "省内A类电源",
        realtime: "省内A类电源",
        unit: "万千瓦",
    },
    Field {
        key: "local",
        name: "地方电源最大出力",
        day: "地方电源出力",
        realtime: "地方电源出力",
        unit: "万千瓦",
    },
    Field {
        key: "west",
        name: "西电东送电力",
        day: "西电东送电力",
        realtime: "西电东送电力",
        unit: "万千瓦",
    },
    Field {
        key: "bclass",
        name: "B类竞价空间最大值",
        day: "B类竞价空间",
        realtime: "B类竞价空间",
        unit: "万千瓦",
    },
    Field {
        key: "maintenance",
        name: "检修容量",
        day: "检修容量",
        realtime: "检修容量",
        unit: "万千瓦",
    },
    Field {
        key: "coal",
        name: "煤机开机台数",
        day: "市场开机",
        realtime: "在线机组数",
        unit: "台",
    },
    Field {
        key: "gas",
        name: "气机开机台数",
        day: "市场开机",
        realtime: "在线机组数",
        unit: "台",
    },
    Field {
        key: "price",
        name: "全省市场均价",
        day: "全省日前均价",
        realtime: "全省实时均价",
        unit: "元/兆瓦时",
    },
];

fn numeric(value: &Value, field: &Field) -> Option<f64> {
    let number = value
        .as_f64()
        .or_else(|| value.as_str()?.trim().parse::<f64>().ok())?;
    (number.is_finite()
        && (field.key == "price" || number >= 0.0)
        && (!matches!(field.key, "coal" | "gas") || number.fract() == 0.0))
        .then_some(number)
}

fn normalize(data: &Value, day: &str) -> Result<Value, String> {
    if data.is_null() {
        return Ok(json!({}));
    }
    let object = data.as_object().ok_or("市场手动数据必须为对象。")?;
    if object
        .keys()
        .any(|k| !["report_date", "day_ahead", "realtime"].contains(&k.as_str()))
    {
        return Err("市场手动数据包含未知参数。".into());
    }
    if let Some(date) = object.get("report_date") {
        if date.as_str() != Some(day) {
            return Err("市场手动数据日期与当前报告日期不一致，请重新填写。".into());
        }
    }
    let mut result = Map::new();
    for period in ["day_ahead", "realtime"] {
        let Some(entries) = object.get(period) else {
            continue;
        };
        let entries = entries.as_object().ok_or("市场指标必须为对象。")?;
        if entries.keys().any(|k| !FIELDS.iter().any(|f| f.key == k)) {
            return Err("市场手动数据包含未知指标。".into());
        }
        let mut values = Map::new();
        for field in &FIELDS {
            let Some(pair) = entries.get(field.key) else {
                continue;
            };
            let pair = pair
                .as_object()
                .ok_or_else(|| format!("{}请分别填写当前值和对比值。", field.name))?;
            if pair
                .keys()
                .any(|k| !["value", "comparison"].contains(&k.as_str()))
            {
                return Err(format!("{}包含未知字段。", field.name));
            }
            let mut parsed = Map::new();
            for (key, value) in pair {
                if value.is_null() || value.as_str().is_some_and(|s| s.trim().is_empty()) {
                    continue;
                }
                let number = numeric(value, field).ok_or_else(|| {
                    format!(
                        "{}的{}请输入{}。",
                        field.name,
                        if key == "value" {
                            "当前值"
                        } else {
                            "对比值"
                        },
                        if matches!(field.key, "coal" | "gas") {
                            "非负整数"
                        } else if field.key == "price" {
                            "有限数值"
                        } else {
                            "非负数值"
                        }
                    )
                })?;
                parsed.insert(key.clone(), json!(number));
            }
            if !parsed.is_empty() {
                values.insert(field.key.into(), Value::Object(parsed));
            }
        }
        if !values.is_empty() {
            result.insert(period.into(), Value::Object(values));
        }
    }
    if !result.is_empty() {
        result.insert("report_date".into(), json!(day));
    }
    Ok(Value::Object(result))
}

fn dated(card: &Value, key: &str, expected: &str) -> bool {
    card.get(key).is_none_or(|v| v.as_str() == Some(expected))
}

fn set_values(
    card: &mut Value,
    value: Option<f64>,
    baseline: Option<f64>,
    current: &str,
    comparison: &str,
) {
    card["value"] = json!(value);
    card["comparison_value"] = json!(baseline);
    card["delta"] = json!(value.zip(baseline).map(|(a, b)| a - b));
    card["pending"] = json!(value.is_none());
    card["value_date"] = json!(current);
    card["comparison_date"] = json!(comparison);
    if let Some(object) = card.as_object_mut() {
        if baseline.is_some() {
            object.remove("comparison_note");
        } else {
            object.insert("comparison_note".into(), json!("对比数据待更新"));
        }
        if value.is_some() {
            object.remove("pending_reason");
        }
        // Cached presentation strings must not override newly computed values.
        for key in ["value_text", "delta_text"] {
            object.remove(key);
        }
    }
}

fn merge(payload: &Value) -> Result<Value, String> {
    let period = payload["period"].as_str().ok_or("缺少市场时段。")?;
    if !["day_ahead", "realtime"].contains(&period) {
        return Err("市场时段无效。".into());
    }
    let current = payload["current_date"].as_str().ok_or("缺少当前日期。")?;
    let comparison = payload["comparison_date"]
        .as_str()
        .ok_or("缺少对比日期。")?;
    let mut cards = payload["cards"]
        .as_array()
        .ok_or("市场卡片必须为列表。")?
        .clone();
    if cards.iter().any(|c| !c.is_object()) {
        return Err("市场卡片必须为对象。".into());
    }
    let data = normalize(
        &payload["data"],
        payload["report_date"].as_str().ok_or("缺少报告日期。")?,
    )?;
    let entries = &data[period];
    let mut missing = Vec::new();
    let mut used_count = 0;
    for field in &FIELDS {
        let label = if period == "day_ahead" {
            field.day
        } else {
            field.realtime
        };
        let index = cards
            .iter()
            .position(|c| c["label"] == label)
            .unwrap_or_else(|| {
                cards.push(json!({"label":label,"unit":field.unit}));
                cards.len() - 1
            });
        let card = &mut cards[index];
        let pair = &entries[field.key];
        let thermal = matches!(field.key, "coal" | "gas");
        let (mut value, mut baseline) = if thermal {
            let b = &card["thermal_breakdown"];
            (
                numeric(&b[field.key], field),
                numeric(&b[format!("{}_comparison", field.key)], field),
            )
        } else {
            let v = if card["pending"] == true {
                None
            } else {
                numeric(&card["value"], field)
            };
            let b = numeric(&card["comparison_value"], field).or_else(|| {
                let delta = card["delta"].as_f64().filter(|d| d.is_finite())?;
                numeric(&json!(v? - delta), field)
            });
            (v, b)
        };
        if !dated(card, "value_date", current) {
            value = None;
        }
        if !dated(card, "comparison_date", comparison) {
            baseline = None;
        }
        let mut used = Vec::new();
        for (key, old) in [("value", &mut value), ("comparison", &mut baseline)] {
            let source_key = format!("{key}_source_kind");
            let source = card
                .get(&source_key)
                .or_else(|| card.get("source_kind"))
                .and_then(Value::as_str);
            if old.is_none() || (!thermal && source == Some("workbook")) {
                if let Some(number) = numeric(&pair[key], field) {
                    *old = Some(number);
                    used.push(key);
                    used_count += 1;
                    if !thermal {
                        card[&source_key] = json!("manual");
                    }
                }
            }
        }
        if value.is_none() {
            missing.push(format!("{}当前值", field.name));
        }
        if baseline.is_none() {
            missing.push(format!("{}对比值", field.name));
        }
        if thermal {
            if !card["thermal_breakdown"].is_object() {
                card["thermal_breakdown"] = json!({});
            }
            let b = &mut card["thermal_breakdown"];
            b[field.key] = json!(value);
            b[format!("{}_comparison", field.key)] = json!(baseline);
            b[format!("{}_delta", field.key)] = json!(value.zip(baseline).map(|(a, b)| a - b));
            if !used.is_empty() {
                b["basis"] = json!("website_first_manual_fallback");
                if !b["manual_fields"].is_array() {
                    b["manual_fields"] = json!([]);
                }
                for key in used {
                    b["manual_fields"]
                        .as_array_mut()
                        .unwrap()
                        .push(json!(format!("{}.{key}", field.key)));
                    let prefix = if key == "value" {
                        format!("{}_", field.key)
                    } else {
                        format!("{}_comparison_", field.key)
                    };
                    for suffix in [
                        "mode_frequency",
                        "mode_candidates",
                        "mode_hours",
                        "hour_index",
                    ] {
                        b.as_object_mut()
                            .unwrap()
                            .remove(&format!("{prefix}{suffix}"));
                    }
                }
            }
            // The shared card is finalized only after both fuel fields are sanitized.
            if field.key == "gas" {
                let b = &card["thermal_breakdown"];
                let total = numeric(&b["coal"], &FIELDS[6])
                    .zip(numeric(&b["gas"], &FIELDS[7]))
                    .map(|(a, b)| a + b);
                let base = numeric(&b["coal_comparison"], &FIELDS[6])
                    .zip(numeric(&b["gas_comparison"], &FIELDS[7]))
                    .map(|(a, b)| a + b);
                set_values(card, total, base, current, comparison);
            }
        } else {
            set_values(card, value, baseline, current, comparison);
            if !used.is_empty() {
                card["manual_fields"] = json!(used);
            }
        }
    }
    Ok(json!({"cards":cards,"missing":missing,"used":used_count}))
}

pub fn execute(payload: &Value) -> Result<Value, String> {
    let result = match payload["action"].as_str() {
        Some("normalize") => {
            let data = normalize(
                &payload["data"],
                payload["report_date"].as_str().ok_or("缺少报告日期。")?,
            )?;
            let mut deltas = json!({"day_ahead":{},"realtime":{}});
            for period in ["day_ahead", "realtime"] {
                for field in &FIELDS {
                    let pair = &data[period][field.key];
                    deltas[period][field.key] = json!(numeric(&pair["value"], field)
                        .zip(numeric(&pair["comparison"], field))
                        .map(|(a, b)| a - b));
                }
            }
            json!({"data":data,"deltas":deltas})
        }
        Some("merge") => merge(payload)?,
        _ => return Err("未知市场数据操作。".into()),
    };
    let mut response = result;
    response["protocol"] = json!(1);
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input() -> Value {
        json!({"period":"realtime","current_date":"2026-10-13","comparison_date":"2026-10-13","report_date":"2026-10-15","cards":[],"data":{}})
    }
    #[test]
    fn rejects_invalid_and_stale_manual_values() {
        for value in [json!(true), json!(-1), json!("NaN"), json!("inf")] {
            assert!(
                normalize(&json!({"day_ahead":{"load":{"value":value}}}), "2026-10-15").is_err()
            );
        }
        assert!(normalize(&json!({"realtime":{"coal":{"value":1.5}}}), "2026-10-15").is_err());
        assert!(normalize(&json!({"report_date":"2026-10-14"}), "2026-10-15").is_err());
        assert!(normalize(&json!({"realtime":{"unknown":{}}}), "2026-10-15").is_err());
    }
    #[test]
    fn preserves_website_zero_and_negative_price_and_fills_each_side() {
        let mut p = input();
        p["cards"] = json!([{"label":"全省实时均价","value":-5,"comparison_value":0}, {"label":"统调实际负荷峰值","value":null,"comparison_value":15}]);
        p["data"] = json!({"realtime":{"price":{"value":999,"comparison":999},"load":{"value":18,"comparison":999}}});
        let r = merge(&p).unwrap();
        assert_eq!(r["cards"][0]["value"], -5.0);
        assert_eq!(r["cards"][0]["delta"], -5.0);
        assert_eq!(r["cards"][1]["comparison_value"], 15.0);
        assert_eq!(r["cards"][1]["delta"], 3.0);
        assert_eq!(r["used"], 1);
    }
    #[test]
    fn replaces_workbook_but_keeps_website_baseline() {
        let mut p = input();
        p["cards"] = json!([{"label":"全省实时均价","value":10,"comparison_value":8,"value_source_kind":"workbook","comparison_source_kind":"website"}]);
        p["data"] = json!({"realtime":{"price":{"value":12,"comparison":99}}});
        let r = merge(&p).unwrap();
        assert_eq!(r["cards"][0]["delta"], 4.0);
    }
    #[test]
    fn fills_partial_fuels_and_rejects_wrong_date() {
        let mut p = input();
        p["cards"] = json!([{"label":"在线机组数","thermal_breakdown":{"coal":100,"coal_comparison":99,"gas":null,"gas_comparison":20}}, {"label":"全省实时均价","value":500,"comparison_value":400,"value_date":"2026-10-12"}]);
        p["data"] = json!({"realtime":{"coal":{"value":999},"gas":{"value":0,"comparison":999},"price":{"value":300}}});
        let r = merge(&p).unwrap();
        assert_eq!(r["cards"][0]["value"], 100.0);
        assert_eq!(r["cards"][0]["delta"], -19.0);
        assert_eq!(r["cards"][0]["thermal_breakdown"]["gas_delta"], -20.0);
        assert_eq!(r["cards"][1]["value"], 300.0);
    }
}
