//! Value/number-format reader; full layout and template editing stay in Python.
use calamine::{open_workbook, Data, Reader as CalamineReader, Xlsx};
use quick_xml::{events::Event, Reader};
use serde_json::{json, Value};
use std::{collections::BTreeMap, fs::File, io::Read};
use zip::ZipArchive;

type Error = Box<dyn std::error::Error>;
type Attrs = BTreeMap<String, String>;

fn xml(zip: &mut ZipArchive<File>, path: &str) -> Result<String, Error> {
    let mut text = String::new();
    zip.by_name(path)?.read_to_string(&mut text)?;
    Ok(text)
}

fn tags(text: &str, mut visit: impl FnMut(&str, Attrs) -> Result<(), Error>) -> Result<(), Error> {
    let mut reader = Reader::from_str(text);
    reader.config_mut().expand_empty_elements = true;
    loop {
        match reader.read_event()? {
            Event::Start(e) => {
                let name = String::from_utf8(e.local_name().as_ref().to_vec())?;
                let mut attrs = Attrs::new();
                for attr in e.attributes() {
                    let attr = attr?;
                    attrs.insert(
                        String::from_utf8(attr.key.as_ref().to_vec())?,
                        attr.decoded_and_normalized_value(
                            quick_xml::XmlVersion::Implicit1_0,
                            reader.decoder(),
                        )?
                        .into_owned(),
                    );
                }
                visit(&name, attrs)?;
            }
            Event::End(e) => visit(
                &format!("/{}", std::str::from_utf8(e.local_name().as_ref())?),
                Attrs::new(),
            )?,
            Event::Eof => break,
            Event::Text(e) => {
                visit(
                    "#text",
                    BTreeMap::from([("value".to_string(), e.decode()?.into_owned())]),
                )?;
            }
            _ => (),
        }
    }
    Ok(())
}

fn coordinate(text: &str) -> Result<(u32, u32), Error> {
    let mut col = 0_u32;
    let mut digits = String::new();
    for c in text.chars().filter(|c| *c != '$') {
        if c.is_ascii_uppercase() && digits.is_empty() {
            col = col
                .checked_mul(26)
                .and_then(|n| n.checked_add(c as u32 - 'A' as u32 + 1))
                .ok_or("column overflow")?;
        } else if c.is_ascii_digit() {
            digits.push(c);
        } else {
            return Err("unsupported cell coordinate".into());
        }
    }
    let row = digits.parse::<u32>()?;
    if row == 0 || col == 0 || row > 1_048_576 || col > 16_384 {
        return Err("invalid cell coordinate".into());
    }
    Ok((row, col))
}

fn sheet_path(target: &str) -> Result<String, Error> {
    let full = if target.starts_with('/') {
        target[1..].to_string()
    } else {
        format!("xl/{target}")
    };
    let mut parts = Vec::new();
    for part in full.split('/') {
        match part {
            "" | "." => (),
            ".." => {
                parts.pop().ok_or("invalid worksheet relationship")?;
            }
            other => parts.push(other),
        }
    }
    Ok(parts.join("/"))
}

fn scalar(cell: &Data) -> Result<Value, Error> {
    match cell {
        Data::Int(value) => Ok(json!(value)),
        Data::Float(value) if value.is_finite() => Ok(json!(value)),
        Data::Float(_) => Ok(Value::Null), // raw numeric metadata overrides non-finite XML values
        Data::DateTime(value) => Ok(json!({"excel_date": value.as_f64()})),
        Data::DateTimeIso(value) | Data::DurationIso(value) => Ok(json!({"iso_date": value})),
        Data::Error(value) => Ok(json!({"error": value.to_string()})),
        _ => super::cell_value(cell).map_err(Into::into),
    }
}

fn frame_scalar(cell: &Data) -> Result<Value, Error> {
    let number = match cell {
        Data::Empty => return Ok(Value::String(String::new())),
        Data::Float(value) if value.is_finite() => Some(*value),
        Data::DateTime(value) => Some(value.as_f64()),
        _ => None,
    };
    if let Some(value) = number {
        if value.fract() == 0.0 && value.abs() < (1_u64 << 53) as f64 {
            return Ok(json!(value as i64));
        }
        return Ok(json!(value));
    }
    scalar(cell)
}

pub fn read(path: &str, selected: &[String]) -> Result<Value, Error> {
    read_impl(path, selected, false)
}

pub fn frame(path: &str, selected: &[String]) -> Result<Value, Error> {
    read_impl(path, selected, true)
}

fn read_impl(path: &str, selected: &[String], compact: bool) -> Result<Value, Error> {
    let mut zip = ZipArchive::new(File::open(path)?)?;
    let workbook = xml(&mut zip, "xl/workbook.xml")?;
    let mut epoch1904 = false;
    let mut names = Vec::new();
    tags(&workbook, |name, a| {
        if name == "workbookPr" {
            epoch1904 = matches!(a.get("date1904").map(String::as_str), Some("1" | "true"));
        }
        if name == "sheet" {
            let id = a
                .iter()
                .find(|(key, _)| key.ends_with(":id"))
                .ok_or("missing sheet relationship")?
                .1;
            names.push((
                a.get("name").ok_or("missing sheet name")?.clone(),
                id.clone(),
            ));
        }
        Ok(())
    })?;
    let mut rels = BTreeMap::new();
    tags(&xml(&mut zip, "xl/_rels/workbook.xml.rels")?, |name, a| {
        if name == "Relationship" {
            if let (Some(id), Some(target)) = (a.get("Id"), a.get("Target")) {
                rels.insert(id.clone(), target.clone());
            }
        }
        Ok(())
    })?;
    let mut format_ids = Vec::new();
    let mut custom_formats = BTreeMap::new();
    if let Ok(styles) = xml(&mut zip, "xl/styles.xml") {
        let mut cell_xfs = false;
        tags(&styles, |name, a| {
            if name == "numFmt" {
                custom_formats.insert(
                    a.get("numFmtId").ok_or("missing format id")?.clone(),
                    a.get("formatCode").ok_or("missing format code")?.clone(),
                );
            }
            if name == "cellXfs" {
                cell_xfs = true;
            }
            if name == "/cellXfs" {
                cell_xfs = false;
            }
            if cell_xfs && name == "xf" {
                format_ids.push(
                    a.get("numFmtId")
                        .map(|s| s.parse::<u32>())
                        .transpose()?
                        .unwrap_or(0),
                );
            }
            Ok(())
        })?;
    }
    if format_ids.is_empty() {
        format_ids.push(0);
    }
    let potential_dates: Vec<bool> = format_ids
        .iter()
        .map(|id| {
            if *id >= 164 {
                custom_formats
                    .get(&id.to_string())
                    .is_some_and(|fmt| fmt.to_ascii_lowercase().contains(['y', 'm', 'd', 'h', 's']))
            } else {
                matches!(*id, 14..=36 | 45..=47 | 50..=58)
            }
        })
        .collect();
    if compact {
        let preferred = selected
            .iter()
            .find(|name| names.iter().any(|(n, _)| n == *name))
            .cloned()
            .or_else(|| names.first().map(|(n, _)| n.clone()));
        names.retain(|(name, _)| Some(name) == preferred.as_ref());
    }
    let mut book: Xlsx<_> = open_workbook(path)?;
    let mut sheets = Vec::new();
    for (name, id) in names {
        if !compact && !selected.is_empty() && !selected.contains(&name) {
            continue;
        }
        let text = xml(
            &mut zip,
            &sheet_path(rels.get(&id).ok_or("missing worksheet relationship")?)?,
        )?;
        let mut styles = BTreeMap::<String, usize>::new();
        let mut types = BTreeMap::<String, String>::new();
        let mut merges = Vec::new();
        let mut raw_numbers = BTreeMap::new();
        let mut current_cell = String::new();
        let mut numeric_cell = false;
        let mut in_value = false;
        let (mut actual_row, mut actual_col, mut declared_row, mut declared_col) = (0, 0, 0, 0);
        // Worksheet hot path: avoid allocating an attribute map and callback
        // strings for every XML event (most events need no metadata at all).
        let mut parser = Reader::from_str(&text);
        parser.config_mut().expand_empty_elements = true;
        loop {
            match parser.read_event()? {
                Event::Start(element) => match element.local_name().as_ref() {
                    b"c" => {
                        let mut position = None;
                        let mut kind = None;
                        let mut style = 0;
                        for attr in element.attributes() {
                            let attr = attr?;
                            match attr.key.as_ref() {
                                b"r" | b"t" | b"s" => {
                                    let value = attr
                                        .decoded_and_normalized_value(
                                            quick_xml::XmlVersion::Implicit1_0,
                                            parser.decoder(),
                                        )?
                                        .into_owned();
                                    match attr.key.as_ref() {
                                        b"r" => position = Some(value),
                                        b"t" => kind = Some(value),
                                        _ => style = value.parse::<usize>()?,
                                    }
                                }
                                _ => (),
                            }
                        }
                        let pos = position.ok_or("missing cell coordinate")?;
                        let (row, col) = coordinate(&pos)?;
                        actual_row = actual_row.max(row);
                        actual_col = actual_col.max(col);
                        numeric_cell = kind.as_deref().is_none_or(|kind| kind == "n");
                        if !compact {
                            if let Some(kind) = kind {
                                types.insert(pos.clone(), kind);
                            }
                        }
                        if !compact || potential_dates.get(style).copied().unwrap_or(true) {
                            styles.insert(pos.clone(), style);
                        }
                        current_cell = pos;
                    }
                    b"v" => in_value = true,
                    b"row" | b"dimension" | b"mergeCell" => {
                        let mut found = element.local_name().as_ref() == b"row";
                        for attr in element.attributes() {
                            let attr = attr?;
                            if (element.local_name().as_ref() == b"row"
                                && attr.key.as_ref() == b"r")
                                || (element.local_name().as_ref() != b"row"
                                    && attr.key.as_ref() == b"ref")
                            {
                                found = true;
                                let value = attr.decoded_and_normalized_value(
                                    quick_xml::XmlVersion::Implicit1_0,
                                    parser.decoder(),
                                )?;
                                match element.local_name().as_ref() {
                                    b"row" => actual_row = actual_row.max(value.parse()?),
                                    b"dimension" => {
                                        (declared_row, declared_col) =
                                            coordinate(value.rsplit(':').next().unwrap())?;
                                    }
                                    _ => merges.push(value.into_owned()),
                                }
                            }
                        }
                        if !found {
                            return Err("missing worksheet reference".into());
                        }
                    }
                    _ => (),
                },
                Event::Text(event) if in_value && numeric_cell => {
                    let value = event.decode()?;
                    if value.parse::<f64>().is_ok_and(|v| {
                        !compact || !v.is_finite() || v.abs() >= (1_u64 << 53) as f64
                    }) {
                        raw_numbers.insert(current_cell.clone(), value.into_owned());
                    }
                }
                Event::End(element) if element.local_name().as_ref() == b"v" => in_value = false,
                Event::Eof => break,
                _ => (),
            }
        }
        // Metadata owns its strings. Release the expanded XML before calamine
        // allocates the value grid, instead of holding both large buffers.
        drop(parser);
        drop(text);
        let range = book.worksheet_range(&name)?;
        let (row_offset, col_offset) = range.start().unwrap_or((0, 0));
        let mut object_cells = Vec::new();
        let rows = range
            .rows()
            .enumerate()
            .map(|(rr, row)| {
                row.iter()
                    .enumerate()
                    .map(|(cc, cell)| {
                        let value = if compact {
                            frame_scalar(cell)?
                        } else {
                            scalar(cell)?
                        };
                        if compact && value.is_object() {
                            object_cells
                                .push((rr as u32 + row_offset + 1, cc as u32 + col_offset + 1));
                        }
                        Ok(value)
                    })
                    .collect::<Result<Vec<_>, Error>>()
            })
            .collect::<Result<Vec<_>, Error>>()?;
        drop(range);
        let mut sheet = json!({"name":name, "row_offset":row_offset, "col_offset":col_offset,
            "styles":styles, "types":types, "merges":merges, "raw_numbers":raw_numbers,
            "prepared_frame":compact, "object_cells":object_cells,
            "max_row":if declared_row == 0 {actual_row} else {declared_row},
            "max_column":if declared_col == 0 {actual_col} else {declared_col},
            "actual_max_row":actual_row, "actual_max_column":actual_col});
        // json! serializes borrowed Vec<Value>s by cloning their contents.
        // Move the already-built rows and sheets into the response instead.
        sheet["rows"] = Value::Array(rows.into_iter().map(Value::Array).collect());
        sheets.push(sheet);
    }
    let mut result = json!({"protocol":1, "epoch1904":epoch1904,
        "format_ids":format_ids, "custom_formats":custom_formats});
    result["sheets"] = Value::Array(sheets);
    Ok(result)
}
