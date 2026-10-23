//! Bulk XLSX transport. Business rules and Decimal arithmetic stay in Python.
use calamine::{open_workbook, Data, Reader, Xlsx};
use rust_xlsxwriter::Workbook;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{self, BufReader, BufWriter, Write};
mod cells;
mod compute;
mod frame;
mod wps;

#[derive(Deserialize)]
struct Request {
    protocol: u32,
    operation: String,
    path: String,
    #[serde(default)]
    sheets: Vec<Sheet>,
    #[serde(default)]
    selected: Vec<String>,
    #[serde(default)]
    paths: Vec<String>,
    #[serde(default)]
    payload: Value,
}

#[derive(Deserialize, Serialize)]
struct Sheet {
    name: String,
    rows: Vec<Vec<Value>>,
}

fn cell_value(cell: &Data) -> Result<Value, String> {
    match cell {
        Data::Empty => Ok(Value::Null),
        Data::String(s) => Ok(Value::String(s.clone())),
        Data::Int(n) if n.unsigned_abs() <= (1_u64 << 53) => Ok(Value::from(*n)),
        Data::Float(n) if n.is_finite() && n.abs() <= (1_u64 << 53) as f64 => Ok(Value::from(*n)),
        Data::Bool(b) => Ok(Value::Bool(*b)),
        // Dates and errors need openpyxl's type/format interpretation.
        _ => Err("unsupported cell type; use Python backend".into()),
    }
}

fn execute(request: Request) -> Result<Value, Box<dyn std::error::Error>> {
    if request.protocol != 1 {
        return Err("unsupported price XLSX protocol".into());
    }
    match request.operation.as_str() {
        "capabilities" => Ok(
            serde_json::json!({"protocol":1,"stream":true,"operations":["unmix","sheet_data","read_frame","write_frame","wps_url_choices"]}),
        ),
        "wps_url_choices" => wps::url_choices(&request.payload),
        "unmix" => compute::unmix(&request.payload),
        "sheet_data" => compute::sheet_data(&request.payload),
        "write_frame" => frame::write(&request.payload),
        "read_cells" => cells::read(&request.path, &request.selected),
        "read_frame" => cells::frame(&request.path, &request.selected),
        "read_cells_many" => {
            let books: Vec<Value> = request
                .paths
                .iter()
                .map(|path| {
                    cells::read(path, &request.selected)
                        .unwrap_or_else(|e| serde_json::json!({"error": e.to_string()}))
                })
                .collect();
            Ok(serde_json::json!({"protocol":1, "books":books}))
        }
        "read" => {
            let mut book: Xlsx<_> = open_workbook(&request.path)?;
            let mut sheets = Vec::new();
            for name in book.sheet_names().to_vec() {
                let range = book.worksheet_range(&name)?;
                let mut rows = Vec::new();
                if let Some((row_offset, col_offset)) = range.start() {
                    // The Python parser requires headers on row 1, even when
                    // a worksheet's actual used range starts below it.
                    if row_offset > 0 {
                        rows.push(Vec::new());
                    } else {
                        for row in range.rows() {
                            let mut values = vec![Value::Null; col_offset as usize];
                            for cell in row {
                                values.push(cell_value(cell)?);
                            }
                            rows.push(values);
                        }
                    }
                }
                sheets.push(Sheet { name, rows });
            }
            Ok(serde_json::json!({"protocol": 1, "sheets": sheets}))
        }
        "write" => {
            let mut book = Workbook::new();
            for sheet in request.sheets {
                let ws = book.add_worksheet();
                ws.set_name(&sheet.name)?;
                for (row, cells) in sheet.rows.iter().enumerate() {
                    let row = u32::try_from(row)?;
                    for (col, cell) in cells.iter().enumerate() {
                        let col = u16::try_from(col)?;
                        match cell {
                            Value::Null => (),
                            Value::String(s) => {
                                ws.write_string(row, col, s)?;
                            }
                            Value::Number(n) => {
                                ws.write_number(row, col, n.as_f64().ok_or("invalid number")?)?;
                            }
                            Value::Bool(b) => {
                                ws.write_boolean(row, col, *b)?;
                            }
                            _ => return Err("unsupported output cell".into()),
                        }
                    }
                }
            }
            book.save(&request.path)?;
            Ok(serde_json::json!({"protocol": 1, "written": true}))
        }
        _ => Err("unknown operation".into()),
    }
}

fn main() {
    if std::env::args().any(|a| a == "--serve") {
        use std::io::BufRead;
        for line in BufReader::new(io::stdin()).lines() {
            let response = line
                .map_err(|e| e.to_string())
                .and_then(|s| serde_json::from_str::<Request>(&s).map_err(|e| e.to_string()))
                .and_then(|r| execute(r).map_err(|e| e.to_string()))
                .unwrap_or_else(|e| serde_json::json!({"protocol":1,"error":e}));
            println!("{}", response);
            if io::stdout().flush().is_err() {
                break;
            }
        }
        return;
    }
    let result = serde_json::from_reader::<_, Request>(BufReader::new(io::stdin()))
        .map_err(|e| e.to_string())
        .and_then(|r| execute(r).map_err(|e| e.to_string()));
    match result {
        Ok(value) => {
            let mut output = BufWriter::new(io::stdout());
            if serde_json::to_writer(&mut output, &value).is_err() || output.flush().is_err() {
                std::process::exit(1);
            }
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
