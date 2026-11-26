//! Inspect formula caches without allocating a tree for the worksheet.
use crate::cells::coordinate;
use quick_xml::{
    events::{BytesStart, Event},
    name::{Namespace, ResolveResult},
    Decoder, NsReader, XmlVersion,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    fs::File,
    io::{BufRead, BufReader},
};
use zip::ZipArchive;

type Error = Box<dyn std::error::Error>;
type Bounds = (u32, u32, u32, u32);
const SPREADSHEET: &[u8] = b"http://schemas.openxmlformats.org/spreadsheetml/2006/main";

#[derive(Deserialize)]
struct Selection {
    part: String,
    regions: Vec<Bounds>,
}

fn formula_bounds(reference: &str) -> Result<Bounds, Error> {
    let (first, last) = reference.split_once(':').unwrap_or((reference, reference));
    let (top, left) = coordinate(first)?;
    let (bottom, right) = coordinate(last)?;
    Ok((left, top, right, bottom))
}

fn matching_formula(
    formula: &BytesStart<'_>,
    cell_ref: &str,
    decoder: Decoder,
    regions: &[Bounds],
) -> Result<bool, Error> {
    let range = formula.try_get_attribute("ref")?;
    let reference = range
        .as_ref()
        .map(|attr| attr.decoded_and_normalized_value(XmlVersion::Implicit1_0, decoder))
        .transpose()?;
    let (left, top, right, bottom) = formula_bounds(reference.as_deref().unwrap_or(cell_ref))?;
    Ok(regions.iter().any(|&(min_col, min_row, max_col, max_row)| {
        left <= max_col && min_col <= right && top <= max_row && min_row <= bottom
    }))
}

fn missing_cache(source: impl BufRead, regions: &[Bounds]) -> Result<Option<String>, Error> {
    let mut reader = NsReader::from_reader(source);
    reader.config_mut().expand_empty_elements = true;
    let mut buffer = Vec::new();
    let mut depth = 0;
    let mut worksheet = false;
    let mut sheet_data = false;
    let mut row = false;
    let mut cell = false;
    let mut cell_ref = String::new();
    let mut string_cache = false;
    let mut formula = None;
    let mut cache = None;
    let mut read_cache_text = false;
    let mut missing = None;

    loop {
        let decoder = reader.decoder();
        let (namespace, event) = reader.read_resolved_event_into(&mut buffer)?;
        let main = match namespace {
            ResolveResult::Bound(Namespace(name)) => name == SPREADSHEET,
            ResolveResult::Unbound => false,
            ResolveResult::Unknown(_) => return Err("unbound worksheet XML prefix".into()),
        };
        match event {
            Event::Start(element) => {
                depth += 1;
                let name = element.local_name();
                match depth {
                    1 if !worksheet && main && name.as_ref() == b"worksheet" => worksheet = true,
                    1 => return Err("unsupported worksheet XML root".into()),
                    2 => sheet_data = main && name.as_ref() == b"sheetData",
                    3 => row = sheet_data && main && name.as_ref() == b"row",
                    4 => {
                        cell = row && main && name.as_ref() == b"c" && missing.is_none();
                        if cell {
                            cell_ref.clear();
                            string_cache = false;
                            formula = None;
                            cache = None;
                            for attr in element.attributes() {
                                let attr = attr?;
                                match attr.key.as_ref() {
                                    b"r" => cell_ref.push_str(&attr.decoded_and_normalized_value(
                                        XmlVersion::Implicit1_0,
                                        decoder,
                                    )?),
                                    b"t" => {
                                        string_cache = attr.decoded_and_normalized_value(
                                            XmlVersion::Implicit1_0,
                                            decoder,
                                        )? == "str"
                                    }
                                    _ => (),
                                }
                            }
                        }
                    }
                    5 if cell && main => match name.as_ref() {
                        b"f" if formula.is_none() => {
                            if cell_ref.is_empty() {
                                return Err("formula cell has no coordinate".into());
                            }
                            formula =
                                Some(matching_formula(&element, &cell_ref, decoder, regions)?);
                        }
                        b"v" if cache.is_none() => {
                            cache = Some(string_cache);
                            read_cache_text = true;
                        }
                        _ => (),
                    },
                    6 => read_cache_text = false, // ElementTree uses only the text before the first child.
                    _ => (),
                }
            }
            Event::End(_) => {
                match depth {
                    5 => read_cache_text = false,
                    4 if cell && formula == Some(true) && cache != Some(true) => {
                        missing = Some(cell_ref.clone());
                    }
                    0 => return Err("unbalanced worksheet XML".into()),
                    _ => (),
                }
                depth -= 1;
            }
            Event::Text(text) if read_cache_text && !text.is_empty() => cache = Some(true),
            Event::CData(text) if read_cache_text && !text.is_empty() => cache = Some(true),
            Event::GeneralRef(reference) => {
                let name: &[u8] = &reference;
                if !matches!(name, b"amp" | b"lt" | b"gt" | b"apos" | b"quot")
                    && reference.resolve_char_ref()?.is_none()
                {
                    return Err("unsupported worksheet XML entity".into());
                }
                if read_cache_text {
                    cache = Some(true);
                }
            }
            Event::DocType(_) => return Err("unsupported worksheet XML doctype".into()),
            Event::Eof if worksheet && depth == 0 => return Ok(missing),
            Event::Eof => return Err("incomplete worksheet XML".into()),
            _ => (),
        }
        buffer.clear();
    }
}

pub fn inspect(path: &str, payload: &Value) -> Result<Value, Error> {
    let selections: Vec<Selection> = serde_json::from_value(payload.clone())?;
    let mut archive = ZipArchive::new(File::open(path)?)?;
    let mut results = Vec::with_capacity(selections.len());
    for selection in selections {
        let xml = archive.by_name(&selection.part)?;
        results.push(missing_cache(
            BufReader::with_capacity(128 * 1024, xml),
            &selection.regions,
        )?);
    }
    Ok(json!({"protocol": 1, "missing_cells": results}))
}
