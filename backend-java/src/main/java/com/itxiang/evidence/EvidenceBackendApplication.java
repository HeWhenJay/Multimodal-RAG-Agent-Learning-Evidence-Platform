package com.itxiang.evidence;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.kafka.annotation.EnableKafka;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@ConfigurationPropertiesScan
@MapperScan("com.itxiang.evidence.mapper")
@EnableAsync
@EnableScheduling
@EnableKafka
public class EvidenceBackendApplication {

    /**
     * 启动学迹智配 Java 后端服务。
     */
    public static void main(String[] args) {
        SpringApplication.run(EvidenceBackendApplication.class, args);
    }
}
