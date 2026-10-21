use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
type Error = Box<dyn std::error::Error>;
fn floats(value: &Value) -> Result<Vec<f64>, Error> {
    let bytes = STANDARD.decode(value.as_str().ok_or("missing float buffer")?)?;
    if bytes.len() % 8 != 0 {
        return Err("invalid float buffer".into());
    }
    Ok(bytes
        .chunks_exact(8)
        .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
        .collect())
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn mean(a: [f64; 3]) -> f64 {
    (a[0] + a[1] + a[2]) / 3.0
}
fn max(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else {
        a.max(b)
    }
}
fn min(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else {
        a.min(b)
    }
}
pub fn unmix(p: &Value) -> Result<Value, Error> {
    let h = p["height"].as_u64().ok_or("missing height")? as usize;
    let w = p["width"].as_u64().ok_or("missing width")? as usize;
    let pixels = if p["uint8"].as_bool().unwrap_or(false) {
        STANDARD
            .decode(p["pixels"].as_str().ok_or("missing pixels")?)?
            .into_iter()
            .map(f64::from)
            .collect()
    } else {
        floats(&p["pixels"])?
    };
    let profile = p["profile"].as_bool().unwrap_or(false);
    if profile && w == 0 {
        return Err("empty profile".into());
    }
    let bg = floats(&p["background"])?;
    let palette = p["palette"].as_array().ok_or("missing palette")?;
    if palette.len() != 3
        || pixels.len()
            != h.checked_mul(w)
                .and_then(|n| n.checked_mul(3))
                .ok_or("image too large")?
        || bg.len() != h.checked_mul(3).ok_or("background too large")?
    {
        return Err("image shape mismatch".into());
    }
    let mut colors = [None; 3];
    for (i, v) in palette.iter().enumerate() {
        if v.is_null() {
            continue;
        }
        let f = floats(v)?;
        if f.len() != 3 {
            return Err("palette shape mismatch".into());
        }
        colors[i] = Some([f[0], f[1], f[2]]);
    }
    let threshold = if colors.iter().filter(|c| c.is_some()).count() == 1 {
        0.94
    } else {
        0.98
    };
    let mut output = Vec::with_capacity(pixels.len() * 8);
    for y in 0..h {
        let mut row_result = Vec::new();
        let b = [bg[y * 3], bg[y * 3 + 1], bg[y * 3 + 2]];
        let dirs = colors.map(|c| c.map(|v| [v[0] - b[0], v[1] - b[1], v[2] - b[2]]));
        for x in 0..w {
            let off = (y * w + x) * 3;
            let d = [
                pixels[off] - b[0],
                pixels[off + 1] - b[1],
                pixels[off + 2] - b[2],
            ];
            let mut best = dot(d, d);
            let mut result = [0.0; 3];
            for (i, a) in dirs.iter().enumerate() {
                if let Some(a) = a {
                    let weight = (dot(d, *a) / max(dot(*a, *a), 1.0)).clamp(0.0, 1.0);
                    let residual = [
                        d[0] - weight * a[0],
                        d[1] - weight * a[1],
                        d[2] - weight * a[2],
                    ];
                    let error = dot(residual, residual);
                    if error < best {
                        result = [0.0; 3];
                        result[i] = weight;
                        best = error;
                    }
                }
            }
            let single = best;
            for i in 0..3 {
                for j in i + 1..3 {
                    let (Some(a), Some(c)) = (dirs[i], dirs[j]) else {
                        continue;
                    };
                    let aa = dot(a, a);
                    let bb = dot(c, c);
                    let ab = dot(a, c);
                    let ad = dot(d, a);
                    let bd = dot(d, c);
                    let det = aa * bb - ab * ab;
                    let safe = max(det, 1.0);
                    let wa = (ad * bb - bd * ab) / safe;
                    let wb = (bd * aa - ad * ab) / safe;
                    let valid = det > 1.0 && wa > 0.025 && wb > 0.025 && wa + wb <= 1.03;
                    let total = max(1.0, wa + wb);
                    let wa = wa / total;
                    let wb = wb / total;
                    let residual = [
                        d[0] - wa * a[0] - wb * c[0],
                        d[1] - wa * a[1] - wb * c[1],
                        d[2] - wa * a[2] - wb * c[2],
                    ];
                    let error = dot(residual, residual);
                    if valid && error + 36.0 < best {
                        result = [0.0; 3];
                        result[i] = wa;
                        result[j] = wb;
                        best = error;
                    }
                }
            }
            let dm = mean(d);
            let chroma = d.map(|v| v - dm);
            let chroma_norm = dot(chroma, chroma).sqrt();
            for (i, a) in dirs.iter().enumerate() {
                if let Some(a) = a {
                    let am = mean(*a);
                    let ac = a.map(|v| v - am);
                    let norm = dot(ac, ac).sqrt();
                    let cosine = dot(chroma, ac) / max(chroma_norm * norm, 1.0);
                    let weight = (dm / min(am, -1.0)).clamp(0.0, 1.0);
                    if single > 144.0
                        && cosine > threshold
                        && chroma_norm > 1.5
                        && norm > 20.0
                        && weight >= 0.04
                        && mean(b) >= 245.0
                    {
                        result = [0.0; 3];
                        result[i] = weight;
                        best = 0.0;
                    }
                }
            }
            if best > 576.0 {
                result = [0.0; 3];
            }
            for v in &mut result {
                if *v < 0.025 {
                    *v = 0.0;
                }
            }
            if profile {
                row_result.push(result);
            } else {
                for v in result {
                    output.extend_from_slice(&v.to_le_bytes());
                }
            }
        }
        if profile {
            for channel in 0..3 {
                let mut values: Vec<f64> = row_result.iter().map(|r| r[channel]).collect();
                let median = if values.iter().any(|v| v.is_nan()) {
                    f64::NAN
                } else {
                    values.sort_unstable_by(f64::total_cmp);
                    if w % 2 == 1 {
                        values[w / 2]
                    } else {
                        (values[w / 2 - 1] + values[w / 2]) / 2.0
                    }
                };
                output.extend_from_slice(&median.to_le_bytes());
            }
        }
    }
    Ok(json!({"protocol":1,"coverage":STANDARD.encode(output)}))
}

pub fn sheet_data(p: &Value) -> Result<Value, Error> {
    for sheet in p.as_array().ok_or("missing sheets")? {
        let path = sheet["path"].as_str().ok_or("missing sheet path")?;
        let mut xml = String::from("<sheetData>");
        for row in sheet["rows"].as_array().ok_or("missing rows")? {
            xml.push_str("<row");
            for (k, v) in row["attrs"].as_object().ok_or("missing row attributes")? {
                xml.push_str(&format!(
                    " {k}=\"{}\"",
                    quick_xml::escape::escape(v.as_str().ok_or("invalid attribute")?)
                ));
            }
            xml.push('>');
            for cell in row["cells"].as_array().ok_or("missing cells")? {
                xml.push_str("<c");
                for (k, v) in cell["attrs"].as_object().ok_or("missing cell attributes")? {
                    xml.push_str(&format!(
                        " {k}=\"{}\"",
                        quick_xml::escape::escape(v.as_str().ok_or("invalid attribute")?)
                    ));
                }
                xml.push('>');
                if let Some(value) = cell["value"].as_str().filter(|s| !s.is_empty()) {
                    let text = quick_xml::escape::escape(value).replace('\r', "&#13;");
                    let kind = cell["kind"].as_str().unwrap_or("n");
                    if kind == "s" {
                        xml.push_str(&format!("<is><t xml:space=\"preserve\">{text}</t></is>"));
                    } else if kind == "f" {
                        xml.push_str(&format!("<f>{text}</f><v></v>"));
                    } else {
                        xml.push_str(&format!("<v>{text}</v>"));
                    }
                }
                xml.push_str("</c>");
            }
            xml.push_str("</row>");
        }
        xml.push_str("</sheetData>");
        let source = std::fs::read_to_string(path)?;
        if !source.contains("<sheetData></sheetData>") {
            return Err("unsupported worksheet skeleton".into());
        }
        std::fs::write(path, source.replacen("<sheetData></sheetData>", &xml, 1))?;
    }
    Ok(json!({"protocol":1,"written":true}))
}
