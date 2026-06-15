package com.itsheng.evidence;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
@MapperScan("com.itsheng.evidence.mapper")
public class EvidenceBackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(EvidenceBackendApplication.class, args);
    }
}

