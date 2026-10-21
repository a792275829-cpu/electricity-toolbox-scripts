//! Stream already-normalized table cells without constructing Python Cell objects.
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, BufWriter, Write};
type Error = Box<dyn std::error::Error>;

fn column(mut n: usize) -> String {
    let mut result = Vec::new();
    while n > 0 {
        n -= 1;
        result.push(b'A' + (n % 26) as u8);
        n /= 26;
    }
    result.reverse();
    String::from_utf8(result).unwrap()
}

pub fn write(p: &Value) -> Result<Value, Error> {
    let path = p["path"].as_str().ok_or("missing skeleton")?;
    let rows = p["rows_path"].as_str().ok_or("missing rows path")?;
    let source = std::fs::read_to_string(path)?;
    let (head, tail) = source
        .split_once("<sheetData></sheetData>")
        .ok_or("unsupported table skeleton")?;
    let mut out = BufWriter::new(std::fs::File::create(path)?);
    out.write_all(head.as_bytes())?;
    out.write_all(b"<sheetData>")?;
    let mut count = 0;
    for (index, line) in BufReader::new(std::fs::File::open(rows)?)
        .lines()
        .enumerate()
    {
        let cells: Vec<Value> = serde_json::from_str(&line?)?;
        if index >= 1_048_576 || cells.len() > 16_384 {
            return Err("Excel table dimensions exceeded".into());
        }
        let row = index + 1;
        write!(out, "<row r=\"{row}\">")?;
        for (col, cell) in cells.iter().enumerate() {
            if cell.is_null() {
                continue;
            }
            let kind = cell[0].as_str().ok_or("missing cell type")?;
            let value = cell[1].as_str().ok_or("missing cell value")?;
            let style = cell[2].as_u64().ok_or("missing cell style")?;
            let coordinate = format!("{}{row}", column(col + 1));
            write!(out, "<c r=\"{coordinate}\" s=\"{style}\" t=\"{kind}\">")?;
            if !value.is_empty() {
                let text = quick_xml::escape::escape(value).replace('\r', "&#13;");
                match kind {
                    "inlineStr" => write!(out, "<is><t xml:space=\"preserve\">{text}</t></is>")?,
                    _ if cell.get(3).and_then(Value::as_bool).unwrap_or(false) => {
                        write!(out, "<f>{text}</f><v></v>")?
                    }
                    _ => write!(out, "<v>{text}</v>")?,
                }
            }
            out.write_all(b"</c>")?;
        }
        out.write_all(b"</row>")?;
        count += 1;
    }
    out.write_all(b"</sheetData>")?;
    out.write_all(tail.as_bytes())?;
    out.flush()?;
    Ok(json!({"protocol":1,"written":true,"rows":count}))
}
