# NOAA GFS GRIB fixture

Small public regional GRIB2 subset downloaded 2026-11-14 from NOAA NOMADS. Cycle: 2026-11-14 12:00 UTC; forecast hour 009, valid 21:00 UTC. Region: 124–125°E, 19–18°S. Contains surface/2 m/10 m TMP, RH, UGRD, VGRD, PRATE and GUST, including irrelevant surface temperature and duplicate interval-mean precipitation bands to test level/statistic selection.

Source: https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl
Documentation: https://nomads.ncep.noaa.gov/info.php?page=opendap_grib_migration
Public-data terms: https://registry.opendata.aws/noaa-gfs-bdp-pds/

The test decodes weather and validates units/timestamps; it does not fabricate a weather forecast.
