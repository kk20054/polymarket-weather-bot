# Layer 3-4 Forecast And Hourly Consensus Verification

Generated: 2026-07-05T03:16:00.514773+00:00
Window: last 7 days since 2026-06-28T03:16:00.514773+00:00

## Forecast Runs By City

| city | run_count | latest_age_hours | source_distribution |
| --- | --- | --- | --- |
| ankara | 256 | 43.0 | gfs_ensemble:82, ecmwf:82, metar:17, openmeteo_ecmwf_ifs025:15, openmeteo_gfs_seamless:15, openmeteo_icon_seamless:15, openmeteo_gem_seamless:15, openmeteo_ecmwf_aifs025_single:15 |
| atlanta | 602 | 16.2 | ecmwf:83, gfs_ensemble:81, gfs_seamless_short_range:65, openmeteo_ecmwf_ifs025:60, openmeteo_gfs_seamless:60, openmeteo_ecmwf_aifs025_single:57, openmeteo_ncep_nbm_conus:52, openmeteo_icon_seamless:52 |
| buenos-aires | 133 | 48.9 | ecmwf:66, gfs_ensemble:58, metar:9 |
| chicago | 770 | 13.8 | ecmwf:99, gfs_ensemble:93, gfs_seamless_short_range:80, openmeteo_ecmwf_ifs025:74, openmeteo_gfs_seamless:74, openmeteo_ncep_nbm_conus:72, openmeteo_icon_seamless:66, openmeteo_gem_seamless:66 |
| dallas | 547 | 16.1 | ecmwf:80, gfs_ensemble:72, gfs_seamless_short_range:62, openmeteo_ecmwf_ifs025:56, openmeteo_gfs_seamless:56, openmeteo_ncep_nbm_conus:48, openmeteo_icon_seamless:48, openmeteo_gem_seamless:48 |
| hong-kong | 120 | 16.1 | openmeteo_ecmwf_ifs025:24, openmeteo_gfs_seamless:24, openmeteo_icon_seamless:24, openmeteo_gem_seamless:24, openmeteo_ecmwf_aifs025_single:24 |
| london | 166 | 135.3 | ecmwf:80, gfs_ensemble:76, metar:10 |
| lucknow | 135 | 135.2 | ecmwf:64, gfs_ensemble:60, metar:11 |
| miami | 232 | 135.3 | ecmwf:84, gfs_ensemble:76, gfs_seamless_short_range:65, metar:7 |
| munich | 158 | 135.3 | ecmwf:76, gfs_ensemble:72, metar:10 |
| nyc | 595 | 16.1 | ecmwf:84, gfs_ensemble:76, gfs_seamless_short_range:65, openmeteo_ecmwf_ifs025:56, openmeteo_gfs_seamless:56, openmeteo_ncep_nbm_conus:56, openmeteo_icon_seamless:56, openmeteo_gem_seamless:56 |
| paris | 160 | 135.3 | ecmwf:76, gfs_ensemble:72, metar:12 |
| sao-paulo | 127 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:7 |
| seattle | 223 | 135.3 | ecmwf:80, gfs_ensemble:72, gfs_seamless_short_range:62, metar:9 |
| seoul | 143 | 135.3 | ecmwf:72, gfs_ensemble:60, metar:11 |
| shanghai | 415 | 16.1 | ecmwf:68, gfs_ensemble:64, openmeteo_gfs_seamless:56, openmeteo_icon_seamless:56, openmeteo_gem_seamless:56, openmeteo_ecmwf_aifs025_single:56, openmeteo_ecmwf_ifs025:48, metar:11 |
| singapore | 138 | 135.2 | gfs_ensemble:64, ecmwf:64, metar:10 |
| tel-aviv | 130 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:10 |
| tokyo | 515 | 16.1 | gfs_ensemble:64, ecmwf:64, openmeteo_gfs_seamless:64, openmeteo_jma_seamless:64, openmeteo_icon_seamless:64, openmeteo_gem_seamless:64, openmeteo_ecmwf_aifs025_single:64, openmeteo_ecmwf_ifs025:56 |
| toronto | 127 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:7 |
| wellington | 130 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:10 |

## Stale Forecast Cities

| city | run_count | latest_age_hours | source_distribution |
| --- | --- | --- | --- |
| ankara | 256 | 43.0 | gfs_ensemble:82, ecmwf:82, metar:17, openmeteo_ecmwf_ifs025:15, openmeteo_gfs_seamless:15, openmeteo_icon_seamless:15, openmeteo_gem_seamless:15, openmeteo_ecmwf_aifs025_single:15 |
| buenos-aires | 133 | 48.9 | ecmwf:66, gfs_ensemble:58, metar:9 |
| london | 166 | 135.3 | ecmwf:80, gfs_ensemble:76, metar:10 |
| lucknow | 135 | 135.2 | ecmwf:64, gfs_ensemble:60, metar:11 |
| miami | 232 | 135.3 | ecmwf:84, gfs_ensemble:76, gfs_seamless_short_range:65, metar:7 |
| munich | 158 | 135.3 | ecmwf:76, gfs_ensemble:72, metar:10 |
| paris | 160 | 135.3 | ecmwf:76, gfs_ensemble:72, metar:12 |
| sao-paulo | 127 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:7 |
| seattle | 223 | 135.3 | ecmwf:80, gfs_ensemble:72, gfs_seamless_short_range:62, metar:9 |
| seoul | 143 | 135.3 | ecmwf:72, gfs_ensemble:60, metar:11 |
| singapore | 138 | 135.2 | gfs_ensemble:64, ecmwf:64, metar:10 |
| tel-aviv | 130 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:10 |
| toronto | 127 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:7 |
| wellington | 130 | 135.2 | ecmwf:64, gfs_ensemble:56, metar:10 |

## Hourly Consensus Coverage

| city | target_date | expected_rows | actual_rows | coverage |
| --- | --- | --- | --- | --- |
| ankara | 2026-07-03 | 24 | 24 | 100% |
| ankara | 2026-07-06 | 24 | 24 | 100% |
| ankara | 2026-07-07 | 24 | 3 | 12% |
| atlanta | 2026-06-28 | 24 | 24 | 100% |
| atlanta | 2026-06-29 | 24 | 24 | 100% |
| atlanta | 2026-06-30 | 24 | 24 | 100% |
| atlanta | 2026-07-01 | 24 | 21 | 88% |
| atlanta | 2026-07-02 | 24 | 24 | 100% |
| atlanta | 2026-07-03 | 24 | 24 | 100% |
| atlanta | 2026-07-04 | 24 | 24 | 100% |
| atlanta | 2026-07-05 | 24 | 24 | 100% |
| atlanta | 2026-07-06 | 24 | 24 | 100% |
| atlanta | 2026-07-07 | 24 | 24 | 100% |
| atlanta | 2026-07-08 | 24 | 20 | 83% |
| chicago | 2026-06-28 | 24 | 24 | 100% |
| chicago | 2026-06-29 | 24 | 24 | 100% |
| chicago | 2026-06-30 | 24 | 26 | 26/24 |
| chicago | 2026-07-01 | 24 | 24 | 100% |
| chicago | 2026-07-02 | 24 | 24 | 100% |
| chicago | 2026-07-03 | 24 | 24 | 100% |
| chicago | 2026-07-04 | 24 | 24 | 100% |
| chicago | 2026-07-05 | 24 | 24 | 100% |
| chicago | 2026-07-06 | 24 | 24 | 100% |
| chicago | 2026-07-07 | 24 | 24 | 100% |
| chicago | 2026-07-08 | 24 | 19 | 79% |
| dallas | 2026-06-28 | 24 | 24 | 100% |
| dallas | 2026-06-29 | 24 | 24 | 100% |
| dallas | 2026-06-30 | 24 | 24 | 100% |
| dallas | 2026-07-01 | 24 | 21 | 88% |
| dallas | 2026-07-02 | 24 | 24 | 100% |
| dallas | 2026-07-03 | 24 | 24 | 100% |
| dallas | 2026-07-04 | 24 | 24 | 100% |
| dallas | 2026-07-05 | 24 | 24 | 100% |
| dallas | 2026-07-06 | 24 | 24 | 100% |
| dallas | 2026-07-07 | 24 | 24 | 100% |
| dallas | 2026-07-08 | 24 | 19 | 79% |
| hong-kong | 2026-07-04 | 24 | 24 | 100% |
| hong-kong | 2026-07-05 | 24 | 24 | 100% |
| nyc | 2026-06-28 | 24 | 24 | 100% |
| nyc | 2026-06-29 | 24 | 24 | 100% |
| nyc | 2026-06-30 | 24 | 24 | 100% |
| nyc | 2026-07-01 | 24 | 21 | 88% |
| nyc | 2026-07-02 | 24 | 24 | 100% |
| nyc | 2026-07-03 | 24 | 24 | 100% |
| nyc | 2026-07-04 | 24 | 24 | 100% |
| nyc | 2026-07-05 | 24 | 24 | 100% |
| nyc | 2026-07-06 | 24 | 24 | 100% |
| nyc | 2026-07-07 | 24 | 24 | 100% |
| nyc | 2026-07-08 | 24 | 20 | 83% |
| shanghai | 2026-07-04 | 24 | 24 | 100% |
| shanghai | 2026-07-05 | 24 | 24 | 100% |
| tokyo | 2026-06-28 | 24 | 24 | 100% |
| tokyo | 2026-06-29 | 24 | 24 | 100% |
| tokyo | 2026-06-30 | 24 | 24 | 100% |
| tokyo | 2026-07-01 | 24 | 24 | 100% |
| tokyo | 2026-07-02 | 24 | 22 | 92% |
| tokyo | 2026-07-03 | 24 | 24 | 100% |
| tokyo | 2026-07-04 | 24 | 24 | 100% |
| tokyo | 2026-07-05 | 24 | 24 | 100% |
| tokyo | 2026-07-06 | 24 | 24 | 100% |
| tokyo | 2026-07-07 | 24 | 24 | 100% |
| tokyo | 2026-07-08 | 24 | 24 | 100% |
| tokyo | 2026-07-09 | 24 | 9 | 38% |

## Under-24 Hourly Consensus Groups

| city | target_date | expected_rows | actual_rows | coverage |
| --- | --- | --- | --- | --- |
| ankara | 2026-07-07 | 24 | 3 | 12% |
| atlanta | 2026-07-01 | 24 | 21 | 88% |
| atlanta | 2026-07-08 | 24 | 20 | 83% |
| chicago | 2026-07-08 | 24 | 19 | 79% |
| dallas | 2026-07-01 | 24 | 21 | 88% |
| dallas | 2026-07-08 | 24 | 19 | 79% |
| nyc | 2026-07-01 | 24 | 21 | 88% |
| nyc | 2026-07-08 | 24 | 20 | 83% |
| tokyo | 2026-07-02 | 24 | 22 | 92% |
| tokyo | 2026-07-09 | 24 | 9 | 38% |

## Timestamp Alignment Suspects

No timestamp alignment suspects found.

## Layer Conclusion

- Can enter next layer: no
- Blockers:
  - 14 cities have no forecast run newer than 24h
  - 10 city/date consensus groups have fewer than 24 rows
